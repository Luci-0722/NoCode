import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import process from "node:process";
import readline from "node:readline";
import { PassThrough } from "node:stream";

type Role = "user" | "assistant" | "system";

type Message = {
  role: Role;
  content: string;
};

type ToolCall = {
  name: string;
  args?: Record<string, unknown>;
};

type BackendEvent =
  | { type: "hello"; thread_id: string; model: string; subagent_model: string; cwd: string }
  | { type: "status"; thread_id: string; model: string; subagent_model: string; cwd: string }
  | { type: "cleared"; thread_id: string }
  | { type: "text"; delta: string }
  | { type: "tool_start"; name: string; args?: Record<string, unknown> }
  | { type: "tool_end"; name: string }
  | { type: "done" }
  | { type: "error"; message: string }
  | { type: "fatal"; message: string };

const COLOR = {
  reset: "\x1b[0m",
  bold: "\x1b[1m",
  dim: "\x1b[2m",
  soft: "\x1b[38;2;186;198;207m",
  accent: "\x1b[38;2;95;215;175m",
  secondary: "\x1b[38;2;138;153;166m",
  warning: "\x1b[38;2;244;211;94m",
  danger: "\x1b[38;2;255;107;107m",
  user: "\x1b[38;2;126;217;87m",
};

class TypeScriptTui {
  private readonly version = "NoCode";
  private readonly history: Message[] = [];
  private readonly activeTools: ToolCall[] = [];
  private readonly finishedTools: ToolCall[] = [];
  private readonly inputLines: string[] = [""];
  private backend!: ChildProcessWithoutNullStreams;
  private backendBuffer = "";
  private streaming = "";
  private threadId = "";
  private model = "-";
  private subagentModel = "-";
  private cwd = process.cwd();
  private cursorRow = 0;
  private cursorCol = 0;
  private generating = false;
  private exiting = false;
  private lastFrame = "";
  private scrollOffset = 0;
  private rawInputBuffer = "";
  private readonly keyInput = new PassThrough();

  async start(): Promise<void> {
    this.enterAltScreen();
    this.attachExitHandlers();
    this.spawnBackend();
    this.setupInput();
    this.render();
  }

  private attachExitHandlers(): void {
    const cleanup = () => this.shutdown();
    process.on("exit", cleanup);
    process.on("SIGINT", () => {
      this.exiting = true;
      this.shutdown();
      process.exit(0);
    });
    process.on("SIGTERM", () => {
      this.exiting = true;
      this.shutdown();
      process.exit(0);
    });
  }

  private spawnBackend(): void {
    const localPython = process.platform === "win32"
      ? path.join(process.cwd(), ".venv", "Scripts", "python.exe")
      : path.join(process.cwd(), ".venv", "bin", "python");
    const python = process.env.PYTHON_BIN || (fs.existsSync(localPython) ? localPython : (process.platform === "win32" ? "python" : "python3"));
    this.backend = spawn(python, ["-m", "src.backend_stdio"], {
      cwd: process.cwd(),
      stdio: ["pipe", "pipe", "inherit"],
    });

    this.backend.stdout.setEncoding("utf8");
    this.backend.stdout.on("data", (chunk: string) => {
      this.backendBuffer += chunk;
      let newlineIndex = this.backendBuffer.indexOf("\n");
      while (newlineIndex >= 0) {
        const line = this.backendBuffer.slice(0, newlineIndex).trim();
        this.backendBuffer = this.backendBuffer.slice(newlineIndex + 1);
        if (line) {
          this.handleBackendEvent(JSON.parse(line) as BackendEvent);
        }
        newlineIndex = this.backendBuffer.indexOf("\n");
      }
    });

    this.backend.on("exit", (code) => {
      if (this.exiting) {
        return;
      }
      this.pushHistory({
        role: "system",
        content: `backend exited with code ${code ?? "unknown"}`,
      });
      this.generating = false;
      this.render();
    });
  }

  private setupInput(): void {
    readline.emitKeypressEvents(this.keyInput);
    if (process.stdin.isTTY) {
      process.stdin.setRawMode(true);
    }
    process.stdin.resume();
    process.stdin.setEncoding("utf8");
    this.keyInput.setEncoding("utf8");
    this.keyInput.on("keypress", (_str, key) => this.onKeypress(key));
    process.stdin.on("data", (chunk: string | Buffer) => this.onRawInput(String(chunk)));
  }

  private onKeypress(key: readline.Key): void {
    if (key.ctrl && key.name === "c") {
      this.exiting = true;
      this.shutdown();
      process.exit(0);
    }

    if (this.generating) {
      if (key.name === "escape") {
        this.pushHistory({ role: "system", content: "interrupt requested; wait for current backend response to finish" });
        this.render();
      }
      return;
    }

    if (key.name === "return") {
      if (key.shift) {
        this.insertNewline();
      } else {
        this.submitInput();
      }
      return;
    }

    if (key.name === "backspace") {
      this.backspace();
      return;
    }

    if (key.name === "escape") {
      this.clearInput();
      return;
    }

    if (key.name === "up") {
      this.moveCursor(-1, 0);
      return;
    }

    if (key.name === "down") {
      this.moveCursor(1, 0);
      return;
    }

    if (key.name === "left") {
      this.moveCursor(0, -1);
      return;
    }

    if (key.name === "right") {
      this.moveCursor(0, 1);
      return;
    }

    if (key.name === "tab") {
      this.insertText("  ");
      return;
    }

    if (key.name === "pageup") {
      this.scrollTranscript(5);
      return;
    }

    if (key.name === "pagedown") {
      this.scrollTranscript(-5);
      return;
    }

    if (typeof key.sequence === "string" && key.sequence >= " ") {
      this.insertText(key.sequence);
      return;
    }
  }

  private onRawInput(chunk: string): void {
    this.rawInputBuffer += chunk;

    while (this.rawInputBuffer.length > 0) {
      const mouseStart = this.rawInputBuffer.indexOf("\x1b[<");

      if (mouseStart === -1) {
        this.flushKeyboardInput(this.rawInputBuffer);
        this.rawInputBuffer = "";
        return;
      }

      if (mouseStart > 0) {
        this.flushKeyboardInput(this.rawInputBuffer.slice(0, mouseStart));
        this.rawInputBuffer = this.rawInputBuffer.slice(mouseStart);
      }

      const match = this.rawInputBuffer.match(/^\x1b\[<(\d+);(\d+);(\d+)([Mm])/);
      if (match) {
        const code = Number.parseInt(match[1] || "", 10);
        if (!Number.isNaN(code)) {
          if (code === 64) {
            this.scrollTranscript(3);
          } else if (code === 65) {
            this.scrollTranscript(-3);
          }
        }
        this.rawInputBuffer = this.rawInputBuffer.slice(match[0].length);
        continue;
      }

      if (/^\x1b\[<[0-9;]*$/.test(this.rawInputBuffer)) {
        return;
      }

      this.rawInputBuffer = this.rawInputBuffer.slice(1);
    }
  }

  private flushKeyboardInput(text: string): void {
    if (!text) {
      return;
    }
    this.keyInput.write(text);
  }

  private handleBackendEvent(event: BackendEvent): void {
    switch (event.type) {
      case "hello":
      case "status":
        this.threadId = event.thread_id;
        this.model = event.model;
        this.subagentModel = event.subagent_model;
        this.cwd = event.cwd;
        break;
      case "cleared":
        this.threadId = event.thread_id;
        this.history.length = 0;
        this.streaming = "";
        this.activeTools.length = 0;
        this.finishedTools.length = 0;
        this.scrollOffset = 0;
        break;
      case "text":
        this.streaming += event.delta;
        break;
      case "tool_start":
        this.activeTools.push({ name: event.name, args: event.args });
        break;
      case "tool_end": {
        const index = this.activeTools.findIndex((tool) => tool.name === event.name);
        const tool = index >= 0 ? this.activeTools.splice(index, 1)[0] : { name: event.name };
        this.finishedTools.push(tool);
        break;
      }
      case "done":
        if (this.streaming.trim()) {
          this.pushHistory({ role: "assistant", content: this.streaming });
        }
        this.streaming = "";
        this.activeTools.length = 0;
        this.finishedTools.length = 0;
        this.generating = false;
        break;
      case "error":
      case "fatal":
        this.pushHistory({ role: "system", content: `${event.type}: ${event.message}` });
        this.streaming = "";
        this.activeTools.length = 0;
        this.finishedTools.length = 0;
        this.generating = false;
        break;
    }
    this.render();
  }

  private submitInput(): void {
    const text = this.inputLines.join("\n").trim();
    if (!text) {
      return;
    }

    if (text.startsWith("/")) {
      this.runCommand(text);
      return;
    }

    this.pushHistory({ role: "user", content: text });
    this.streaming = "";
    this.activeTools.length = 0;
    this.finishedTools.length = 0;
    this.generating = true;
    this.scrollOffset = 0;
    this.sendBackend({ type: "prompt", text });
    this.clearInput();
    this.render();
  }

  private runCommand(text: string): void {
    const command = text.trim().toLowerCase();
    this.clearInput();

    if (command === "/quit" || command === "/exit") {
      this.exiting = true;
      this.shutdown();
      process.exit(0);
    }

    if (command === "/clear") {
      this.sendBackend({ type: "clear" });
      this.render();
      return;
    }

    if (command === "/session") {
      this.sendBackend({ type: "status" });
      return;
    }

    if (command === "/help") {
      this.pushHistory({
        role: "system",
        content: "Commands: /help /clear /session /quit\nESC clears input\nEnter submits\nShift+Enter inserts newline",
      });
      this.render();
      return;
    }

    this.pushHistory({ role: "system", content: `unknown command: ${text}` });
    this.render();
  }

  private pushHistory(message: Message): void {
    const pinnedToBottom = this.scrollOffset === 0;
    this.history.push(message);
    if (pinnedToBottom) {
      this.scrollOffset = 0;
    }
  }

  private insertText(text: string): void {
    const line = this.inputLines[this.cursorRow];
    this.inputLines[this.cursorRow] = line.slice(0, this.cursorCol) + text + line.slice(this.cursorCol);
    this.cursorCol += text.length;
    this.render();
  }

  private insertNewline(): void {
    const line = this.inputLines[this.cursorRow];
    const before = line.slice(0, this.cursorCol);
    const after = line.slice(this.cursorCol);
    this.inputLines[this.cursorRow] = before;
    this.inputLines.splice(this.cursorRow + 1, 0, after);
    this.cursorRow += 1;
    this.cursorCol = 0;
    this.render();
  }

  private backspace(): void {
    const line = this.inputLines[this.cursorRow];
    if (this.cursorCol > 0) {
      this.inputLines[this.cursorRow] = line.slice(0, this.cursorCol - 1) + line.slice(this.cursorCol);
      this.cursorCol -= 1;
      this.render();
      return;
    }
    if (this.cursorRow > 0) {
      const previous = this.inputLines[this.cursorRow - 1];
      this.cursorCol = previous.length;
      this.inputLines[this.cursorRow - 1] = previous + line;
      this.inputLines.splice(this.cursorRow, 1);
      this.cursorRow -= 1;
      this.render();
    }
  }

  private clearInput(): void {
    this.inputLines.splice(0, this.inputLines.length, "");
    this.cursorRow = 0;
    this.cursorCol = 0;
    this.render();
  }

  private moveCursor(rowDelta: number, colDelta: number): void {
    const nextRow = Math.max(0, Math.min(this.inputLines.length - 1, this.cursorRow + rowDelta));
    const nextCol = Math.max(0, Math.min(this.inputLines[nextRow].length, rowDelta !== 0 ? Math.min(this.cursorCol, this.inputLines[nextRow].length) : this.cursorCol + colDelta));
    this.cursorRow = nextRow;
    this.cursorCol = nextCol;
    this.render();
  }

  private sendBackend(payload: Record<string, unknown>): void {
    this.backend.stdin.write(`${JSON.stringify(payload)}\n`);
  }

  private render(): void {
    const width = process.stdout.columns || 120;
    const height = process.stdout.rows || 40;
    const header = this.renderHeader(width);
    const composer = this.renderComposer(width);
    const footer = this.renderFooter(width);
    const reserved = header.length + composer.length + footer.length;
    const transcriptHeight = Math.max(8, height - reserved);
    const transcript = this.renderTranscript(width, transcriptHeight);
    const frameLines = [...header, ...transcript, ...composer, ...footer];
    const frame = frameLines.join("\n");

    if (frame !== this.lastFrame) {
      process.stdout.write("\x1b[H\x1b[2J");
      process.stdout.write(frame);
      this.lastFrame = frame;
    }

    this.positionCursor(width, header.length + transcript.length);
  }

  private renderHeader(width: number): string[] {
    const title = "NO";
    const meta = `${this.model} · thread ${this.threadId.slice(-8) || "--------"}`;
    const cwd = this.truncate(this.cwd, Math.max(20, width - 1));
    return [
      `${COLOR.accent}${COLOR.bold}${title}${COLOR.reset}  ${COLOR.secondary}${this.truncate(meta, Math.max(12, width - 6))}${COLOR.reset}`,
      `${COLOR.secondary}${cwd}${COLOR.reset}`,
      "",
    ];
  }

  private renderTranscript(width: number, height: number): string[] {
    const blocks = this.buildTranscriptBlocks(width);
    const maxOffset = Math.max(0, blocks.length - height);
    this.scrollOffset = Math.max(0, Math.min(this.scrollOffset, maxOffset));
    const start = Math.max(0, blocks.length - height - this.scrollOffset);
    const visible = blocks.slice(start, start + height);
    const lines: string[] = [];
    lines.push(...visible);
    while (lines.length < height) {
      lines.push("");
    }
    return lines;
  }

  private buildTranscriptBlocks(width: number): string[] {
    const lines: string[] = [];

    if (this.history.length === 0 && !this.generating) {
      lines.push("");
      lines.push(`${COLOR.secondary}  使用 /help 查看命令，直接输入即可开始对话。${COLOR.reset}`);
      return lines;
    }

    for (const message of this.history) {
      lines.push(...this.renderMessageBlock(message.role, message.content, width));
      lines.push("");
    }

    if (this.streaming || this.generating || this.activeTools.length || this.finishedTools.length) {
      lines.push(...this.renderMessageBlock("assistant", this.streaming || "思考中...", width));
      if (this.activeTools.length || this.finishedTools.length) {
        lines.push("");
        for (const summary of this.renderToolSummary(width)) {
          lines.push(summary);
        }
      }
      lines.push("");
    }

    while (lines.length > 0 && !lines[lines.length - 1].trim()) {
      lines.pop();
    }

    return lines;
  }

  private renderMessageBlock(role: Role, content: string, width: number): string[] {
    const availableWidth = Math.max(12, width - 4);
    const prefix = role === "user" ? "❯ " : role === "assistant" ? "⏺ " : "  ";
    const continuation = "  ";
    const wrapped = this.wrap(content || " ", availableWidth);
    return wrapped.map((line, index) => {
      const leader = index === 0 ? prefix : continuation;
      const body = role === "user"
        ? `${COLOR.bold}${line}${COLOR.reset}`
        : role === "assistant"
          ? `${COLOR.soft}${line}${COLOR.reset}`
          : `${COLOR.secondary}${line}${COLOR.reset}`;
      const marker = role === "user"
        ? `${COLOR.user}${COLOR.bold}${leader}${COLOR.reset}`
        : role === "assistant"
          ? `${COLOR.accent}${COLOR.bold}${leader}${COLOR.reset}`
          : `${COLOR.secondary}${leader}${COLOR.reset}`;
      return `${marker}${body}`;
    });
  }

  private renderToolSummary(width: number): string[] {
    const summaries: string[] = [];
    const active = this.activeTools.slice(-3);
    const finished = this.finishedTools.slice(-3);

    if (active.length > 0) {
      summaries.push(
        `${COLOR.warning}  ${this.summarizeToolBucket("Running", active, width - 2)}${COLOR.reset}`,
      );
    }
    if (finished.length > 0) {
      summaries.push(
        `${COLOR.secondary}  ${this.summarizeToolBucket("Used", finished, width - 2)}${COLOR.reset}`,
      );
    }
    return summaries;
  }

  private summarizeToolBucket(label: string, tools: ToolCall[], width: number): string {
    const names = tools.map((tool) => tool.name.replace(/_/g, " "));
    const base = `${label} ${tools.length} tool${tools.length > 1 ? "s" : ""}: ${names.join(", ")}`;
    return this.truncate(base, Math.max(12, width));
  }

  private renderComposer(width: number): string[] {
    const separator = `${COLOR.secondary}${"─".repeat(width)}${COLOR.reset}`;
    const lines = [separator];
    const availableWidth = Math.max(12, width - 4);
    const body = this.inputLines.length ? this.inputLines : [""];
    const wrappedLines = body.flatMap((line, index) => {
      const wrapped = this.wrap(line, availableWidth);
      return wrapped.map((segment, segmentIndex) => {
        const prefix = index === 0 && segmentIndex === 0 ? "❯ " : "  ";
        return `${COLOR.user}${COLOR.bold}${prefix}${COLOR.reset}${segment}`;
      });
    });

    if (wrappedLines.length === 0) {
      wrappedLines.push(`${COLOR.user}${COLOR.bold}❯ ${COLOR.reset}`);
    }

    lines.push(...wrappedLines);
    return lines;
  }

  private renderFooter(width: number): string[] {
    const state = this.generating ? "busy" : "idle";
    const scroll = this.scrollOffset > 0 ? `  scroll +${this.scrollOffset}` : "";
    const text = `Enter submit  Shift+Enter newline  ESC clear  Wheel/PageUp/PageDown history  /help  ${state}  ${this.subagentModel}${scroll}`;
    return ["", `${COLOR.secondary}${this.truncate(text, width)}${COLOR.reset}`];
  }

  private decorateTranscriptLine(line: string): string {
    if (line === "[you]") {
      return `${COLOR.user}${COLOR.bold}${line}${COLOR.reset}`;
    }
    if (line === "[assistant]") {
      return `${COLOR.accent}${COLOR.bold}${line}${COLOR.reset}`;
    }
    if (line === "[system]") {
      return `${COLOR.secondary}${COLOR.bold}${line}${COLOR.reset}`;
    }
    if (line.startsWith("●")) {
      return `${COLOR.warning}${line}${COLOR.reset}`;
    }
    if (line.startsWith("✓")) {
      return `${COLOR.accent}${line}${COLOR.reset}`;
    }
    return line;
  }

  private truncate(text: string, width: number): string {
    if (this.visibleLength(text) <= width) {
      return text;
    }
    if (width <= 0) {
      return "";
    }
    if (width === 1) {
      return "…";
    }
    return `${this.sliceByWidth(text, width - 1)}…`;
  }

  private wrap(text: string, width: number): string[] {
    return text.split("\n").flatMap((line) => {
      if (!line) {
        return [""];
      }
      const parts: string[] = [];
      let remaining = line;
      while (this.visibleLength(remaining) > width) {
        const chunk = this.sliceByWidth(remaining, width);
        parts.push(chunk);
        remaining = remaining.slice(chunk.length);
      }
      parts.push(remaining);
      return parts;
    });
  }

  private visibleLength(text: string): number {
    return Array.from(this.stripAnsi(text)).reduce((sum, char) => sum + this.charWidth(char), 0);
  }

  private sliceByWidth(text: string, width: number): string {
    if (width <= 0) {
      return "";
    }
    let result = "";
    let consumed = 0;
    for (const char of Array.from(text)) {
      const charWidth = this.charWidth(char);
      if (consumed + charWidth > width) {
        break;
      }
      result += char;
      consumed += charWidth;
    }
    return result;
  }

  private stripAnsi(text: string): string {
    return text.replace(/\x1b\[[0-9;]*m/g, "");
  }

  private charWidth(char: string): number {
    const codePoint = char.codePointAt(0);
    if (codePoint === undefined) {
      return 0;
    }
    if (
      codePoint === 0 ||
      codePoint < 32 ||
      (codePoint >= 0x7f && codePoint < 0xa0) ||
      (codePoint >= 0x300 && codePoint <= 0x36f) ||
      (codePoint >= 0x200b && codePoint <= 0x200f) ||
      (codePoint >= 0xfe00 && codePoint <= 0xfe0f)
    ) {
      return 0;
    }
    if (
      codePoint >= 0x1100 &&
      (
        codePoint <= 0x115f ||
        codePoint === 0x2329 ||
        codePoint === 0x232a ||
        (codePoint >= 0x2e80 && codePoint <= 0xa4cf && codePoint !== 0x303f) ||
        (codePoint >= 0xac00 && codePoint <= 0xd7a3) ||
        (codePoint >= 0xf900 && codePoint <= 0xfaff) ||
        (codePoint >= 0xfe10 && codePoint <= 0xfe19) ||
        (codePoint >= 0xfe30 && codePoint <= 0xfe6f) ||
        (codePoint >= 0xff00 && codePoint <= 0xff60) ||
        (codePoint >= 0xffe0 && codePoint <= 0xffe6) ||
        (codePoint >= 0x1f300 && codePoint <= 0x1faf6) ||
        (codePoint >= 0x20000 && codePoint <= 0x3fffd)
      )
    ) {
      return 2;
    }
    return 1;
  }

  private enterAltScreen(): void {
    process.stdout.write("\x1b[?1049h\x1b[?25l\x1b[?1000h\x1b[?1006h");
  }

  private scrollTranscript(delta: number): void {
    const width = process.stdout.columns || 120;
    const height = process.stdout.rows || 40;
    const headerHeight = this.renderHeader(width).length;
    const composerHeight = this.renderComposer(width).length;
    const footerHeight = this.renderFooter(width).length;
    const transcriptHeight = Math.max(8, height - headerHeight - composerHeight - footerHeight);
    const maxOffset = Math.max(0, this.buildTranscriptBlocks(width).length - transcriptHeight);
    const nextOffset = Math.max(0, Math.min(maxOffset, this.scrollOffset + delta));
    if (nextOffset !== this.scrollOffset) {
      this.scrollOffset = nextOffset;
      this.render();
    }
  }

  private positionCursor(width: number, promptStartRow: number): void {
    const availableWidth = Math.max(12, width - 4);
    let visualRowOffset = 1;

    for (let i = 0; i < this.cursorRow; i += 1) {
      const wrapped = this.wrap(this.inputLines[i], availableWidth);
      visualRowOffset += Math.max(1, wrapped.length);
    }

    const currentLine = this.inputLines[this.cursorRow] ?? "";
    const beforeCursor = currentLine.slice(0, this.cursorCol);
    const wrappedBeforeCursor = this.wrap(beforeCursor, availableWidth);
    const cursorLine = wrappedBeforeCursor.length === 0 ? 0 : wrappedBeforeCursor.length - 1;
    const cursorColBase = wrappedBeforeCursor.length === 0
      ? 0
      : this.visibleLength(wrappedBeforeCursor[wrappedBeforeCursor.length - 1]);
    const promptPrefix = cursorLine === 0 ? 2 : 2;
    const row = promptStartRow + visualRowOffset + cursorLine;
    const col = promptPrefix + cursorColBase + 1;
    process.stdout.write(`\x1b[${row};${Math.max(1, col)}H`);
  }

  private shutdown(): void {
    process.stdout.write("\x1b[?1006l\x1b[?1000l\x1b[?25h\x1b[?1049l");
    if (process.stdin.isTTY) {
      process.stdin.setRawMode(false);
    }
    if (this.backend && !this.backend.killed) {
      this.backend.stdin.write(`${JSON.stringify({ type: "exit" })}\n`);
      this.backend.kill();
    }
  }
}

const app = new TypeScriptTui();
void app.start();
