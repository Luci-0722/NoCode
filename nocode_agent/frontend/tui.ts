import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import process from "node:process";
import readline from "node:readline";
import { PassThrough } from "node:stream";

type Role = "user" | "assistant" | "system";

type TextMessage = {
  id: number;
  kind: "message";
  role: Role;
  content: string;
  state?: "queued" | "sent";
};

type ToolCall = {
  id: number;
  kind: "tool";
  name: string;
  args?: Record<string, unknown>;
  output?: string;
  status: "running" | "done";
  expanded: boolean;
  toolCallId?: string;
};

type Message = TextMessage | ToolCall;

type PendingPrompt = {
  messageId: number;
  text: string;
};

type ThreadInfo = {
  thread_id: string;
  preview: string;
  message_count: number;
};

type QuestionOption = {
  label: string;
  description: string;
};

type Question = {
  question: string;
  header?: string;
  options?: QuestionOption[];
  multiSelect?: boolean;
};

type QuestionAnswer = {
  question_index: number;
  selected: string[];
};

type BackendEvent =
  | { type: "hello"; thread_id: string; model: string; subagent_model: string; cwd: string }
  | { type: "status"; thread_id: string; model: string; subagent_model: string; cwd: string }
  | { type: "cleared"; thread_id: string }
  | { type: "text"; delta: string }
  | { type: "retry"; message: string; attempt: number; max_retries: number; delay: number }
  | { type: "tool_start"; name: string; args?: Record<string, unknown>; tool_call_id?: string }
  | { type: "tool_end"; name: string; output?: string; tool_call_id?: string }
  | { type: "question"; questions: Question[]; tool_call_id: string }
  | { type: "done" }
  | { type: "error"; message: string }
  | { type: "fatal"; message: string }
  | { type: "cancelled" }
  | { type: "thread_list"; threads: ThreadInfo[] }
  | { type: "resumed"; thread_id: string; model: string; subagent_model: string; cwd: string }
  | {
      type: "history";
      messages: Array<
        | { role: string; content: string }
        | {
            kind: "tool";
            name: string;
            args?: Record<string, unknown>;
            output?: string;
            tool_call_id?: string;
          }
      >;
    };

const COLOR = {
  reset: "\x1b[0m",
  bold: "\x1b[1m",
  dim: "\x1b[2m",
  italic: "\x1b[3m",
  underline: "\x1b[4m",
  strikethrough: "\x1b[9m",
  soft: "\x1b[38;2;186;198;207m",
  accent: "\x1b[38;2;95;215;175m",
  secondary: "\x1b[38;2;138;153;166m",
  warning: "\x1b[38;2;244;211;94m",
  danger: "\x1b[38;2;255;107;107m",
  user: "\x1b[38;2;126;217;87m",
  selectedBg: "\x1b[48;2;32;48;58m",
  selectedBorder: "\x1b[38;2;95;215;175m",
  selectedText: "\x1b[38;2;230;238;242m",
  selectedSubtle: "\x1b[38;2;168;191;201m",
  md: {
    heading: "\x1b[38;2;95;215;175m",
    headingBold: "\x1b[38;2;95;215;175m",
    // 代码样式改为无深色底，避免出现“黑底黄字”。
    code: "\x1b[38;2;186;198;207m",
    codeBg: "",
    strong: "\x1b[48;2;244;248;255m\x1b[38;2;49;110;201m",
    link: "\x1b[38;2;104;179;215m\x1b[4m",
    blockquote: "\x1b[38;2;139;153;166m",
    hr: "\x1b[38;2;80;80;80m",
    listBullet: "\x1b[38;2;95;215;175m",
    tableBorder: "\x1b[38;2;80;90;100m",
    tableHeader: "\x1b[38;2;186;198;207m",
  },
};

const ENABLE_KITTY_KEYBOARD = "\x1b[>1u";
const DISABLE_KITTY_KEYBOARD = "\x1b[<u";
const ENABLE_MODIFY_OTHER_KEYS = "\x1b[>4;2m";
const DISABLE_MODIFY_OTHER_KEYS = "\x1b[>4m";

class TypeScriptTui {
  private readonly version = "NoCode";
  private readonly history: Message[] = [];
  private readonly inputLines: string[] = [""];
  private readonly pendingPrompts: PendingPrompt[] = [];
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
  private readonly keyInput = new PassThrough();
  private nextMessageId = 1;
  private nextToolId = 1;
  private selectedToolId: number | null = null;
  private followLatestTool = true;
  // ── Resume / session picker state ──────────────────────────
  private readonly resumeMode: boolean;
  private showSessionPicker = false;
  private sessionThreads: ThreadInfo[] = [];
  private sessionPickerIndex = 0;
  private sessionPickerScroll = 0;

  // ── Question mode state ───────────────────────────────────
  private questionMode = false;
  private activeQuestions: Question[] = [];
  private currentQuestionIndex = 0;
  private optionIndex = 0;
  private multiSelected: Set<number> = new Set();
  private otherMode = false;
  private otherText = "";
  private questionAnswers: QuestionAnswer[] = [];

  constructor() {
    this.resumeMode = process.argv.includes("--resume");
  }

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
    this.backend = spawn(python, ["-m", "nocode_agent.backend_stdio"], {
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
          try {
            this.handleBackendEvent(JSON.parse(line) as BackendEvent);
          } catch (error) {
            const message = error instanceof Error ? error.message : String(error);
            this.pushHistory({
              kind: "message",
              role: "system",
              content: `invalid backend event: ${message}\n${line}`,
            });
            this.generating = false;
            this.render();
          }
        }
        newlineIndex = this.backendBuffer.indexOf("\n");
      }
    });

    this.backend.on("exit", (code) => {
      if (this.exiting) {
        return;
      }
      this.pushHistory({
        kind: "message",
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
    // ── Session picker mode ───────────────────────────────
    if (this.showSessionPicker) {
      if (key.name === "up" || (key.ctrl && key.name === "k")) {
        this.moveSessionPicker(-1);
        return;
      }
      if (key.name === "down" || (key.ctrl && key.name === "j")) {
        this.moveSessionPicker(1);
        return;
      }
      if (key.name === "return") {
        this.confirmSessionPicker();
        return;
      }
      if (key.name === "escape") {
        this.showSessionPicker = false;
        this.pushHistory({ kind: "message", role: "system", content: "取消恢复，使用新会话。" });
        this.render();
        return;
      }
      return; // swallow all other keys while picker is active
    }

    // ── Question mode ───────────────────────────────────
    if (this.questionMode) {
      this.handleQuestionKeypress(key);
      return;
    }

    // ── Normal input mode ─────────────────────────────────
    if ((key.ctrl && key.name === "c") || (key.meta && key.name === "c")) {
      this.exiting = true;
      this.shutdown();
      process.exit(0);
    }

    if (key.ctrl && key.name === "o") {
      this.toggleSelectedTool();
      return;
    }

    const isCtrlJ = (key.ctrl && key.name === "j") || key.sequence === "\n";
    const isCtrlK = (key.ctrl && key.name === "k") || key.sequence === "\x0b";

    if (isCtrlJ) {
      this.moveToolSelection(1);
      return;
    }

    if (isCtrlK) {
      this.moveToolSelection(-1);
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
      if (this.inputLines.some((line) => line.length > 0)) {
        this.clearInput();
      } else if (this.generating) {
        this.sendBackend({ type: "cancel" });
      }
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
    if (this.isCtrlCSequence(chunk)) {
      this.exiting = true;
      this.shutdown();
      process.exit(0);
    }

    if (this.isShiftEnterSequence(chunk)) {
      if (!this.showSessionPicker && !this.questionMode) {
        this.insertNewline();
      }
      return;
    }

    // 保留原始键盘输入，不再拦截鼠标事件，交给终端原生选区与复制处理。
    this.flushKeyboardInput(chunk);
  }

  private isShiftEnterSequence(chunk: string): boolean {
    return chunk === "\x1b[13;2u"
      || chunk === "\x1b[13;2~"
      || chunk === "\x1b[27;2;13~"
      || chunk === "\x1b[27;13;2~";
  }

  private isCtrlCSequence(chunk: string): boolean {
    return chunk === "\x03"
      || chunk === "\x1b[99;5u"
      || chunk === "\x1b[99;6u"
      || chunk === "\x1b[27;5;99~"
      || chunk === "\x1b[27;6;99~";
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
        this.threadId = event.thread_id;
        this.model = event.model;
        this.subagentModel = event.subagent_model;
        this.cwd = event.cwd;
        if (this.resumeMode) {
          this.showSessionPicker = true;
          this.sendBackend({ type: "list_threads", source: "tui" });
        }
        break;
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
        this.pendingPrompts.length = 0;
        this.selectedToolId = null;
        this.followLatestTool = true;
        this.scrollOffset = 0;
        break;
      case "text":
        this.streaming += event.delta;
        break;
      case "tool_start":
        this.flushStreamingToHistory();
        this.startToolRun(event.name, event.args, event.tool_call_id);
        break;
      case "tool_end": {
        this.finishToolRun(event.name, event.output, event.tool_call_id);
        break;
      }
      case "question":
        this.flushStreamingToHistory();
        this.questionMode = true;
        this.activeQuestions = event.questions;
        this.currentQuestionIndex = 0;
        this.optionIndex = 0;
        this.multiSelected = new Set();
        this.otherMode = false;
        this.otherText = "";
        this.questionAnswers = [];
        break;
      case "done":
        this.flushStreamingToHistory();
        this.generating = false;
        this.dispatchNextQueuedPrompt();
        break;
      case "retry":
        this.pushHistory({
          kind: "message",
          role: "system",
          content: `⏳ 请求被限流，第 ${event.attempt}/${event.max_retries} 次重试，${event.delay.toFixed(0)}s 后...`,
        });
        this.render();
        break;
      case "error":
      case "fatal":
        this.pushHistory({ kind: "message", role: "system", content: `${event.type}: ${event.message}` });
        this.streaming = "";
        this.generating = false;
        this.pendingPrompts.length = 0;
        break;
      case "cancelled":
        this.pushHistory({ kind: "message", role: "system", content: "⏹ 已中断生成" });
        this.pendingPrompts.length = 0;
        break;
      case "thread_list":
        this.sessionThreads = event.threads;
        this.sessionPickerIndex = 0;
        this.sessionPickerScroll = 0;
        if (this.sessionThreads.length === 0) {
          this.showSessionPicker = false;
          this.pushHistory({ kind: "message", role: "system", content: "没有找到历史会话，将创建新会话。" });
        }
        break;
      case "resumed":
        this.threadId = event.thread_id;
        this.model = event.model;
        this.subagentModel = event.subagent_model;
        this.cwd = event.cwd;
        this.showSessionPicker = false;
        this.sendBackend({ type: "load_history" });
        break;
      case "history":
        for (const msg of event.messages) {
          if ("role" in msg && (msg.role === "user" || msg.role === "assistant" || msg.role === "system")) {
            this.pushHistory({ kind: "message", role: msg.role as Role, content: msg.content });
          } else if ("kind" in msg && msg.kind === "tool") {
            this.pushHistory({
              kind: "tool",
              name: msg.name,
              args: msg.args,
              output: msg.output ?? "",
              status: "done",
              expanded: false,
              toolCallId: msg.tool_call_id,
            });
          }
        }
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

    const messageId = this.pushHistory({
      kind: "message",
      role: "user",
      content: text,
      state: this.generating ? "queued" : "sent",
    });
    if (this.generating) {
      this.pendingPrompts.push({ messageId, text });
    } else {
      this.dispatchPrompt(text, messageId);
    }
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
        kind: "message",
        role: "system",
        content: "Commands: /help /clear /session /quit\nESC clear input / interrupt agent\nEnter submits\nShift+Enter inserts newline\nwheel/PgUp/PgDn 滚动\nCtrl+J/K select tool  Ctrl+O expand tool result\n启动时加 --resume 可选择历史会话恢复",
      });
      this.render();
      return;
    }

    this.pushHistory({ kind: "message", role: "system", content: `unknown command: ${text}` });
    this.render();
  }

  private pushHistory(message: Omit<Message, "id">): number {
    const pinnedToBottom = this.scrollOffset === 0;
    const nextMessage: Message = { ...message, id: this.nextMessageId++ };
    this.history.push(nextMessage);
    if (pinnedToBottom) {
      this.scrollOffset = 0;
    }
    return nextMessage.id;
  }

  private updateMessageState(messageId: number, state: "queued" | "sent"): void {
    const message = this.history.find((entry) => entry.id === messageId);
    if (message?.kind === "message" && message.role === "user") {
      message.state = state;
    }
  }

  private dispatchPrompt(text: string, messageId: number): void {
    this.updateMessageState(messageId, "sent");
    this.streaming = "";
    this.generating = true;
    this.scrollOffset = 0;
    this.sendBackend({ type: "prompt", text });
  }

  private dispatchNextQueuedPrompt(): void {
    const next = this.pendingPrompts.shift();
    if (!next) {
      return;
    }
    this.dispatchPrompt(next.text, next.messageId);
  }

  private flushStreamingToHistory(): void {
    const content = this.streaming.trimEnd();
    if (!content.trim()) {
      this.streaming = "";
      return;
    }
    this.pushHistory({
      kind: "message",
      role: "assistant",
      content,
    });
    this.streaming = "";
  }

  private startToolRun(name: string, args?: Record<string, unknown>, toolCallId?: string): void {
    const run: ToolCall = {
      id: this.nextToolId++,
      kind: "tool",
      name,
      args,
      status: "running",
      expanded: false,
      toolCallId,
    };
    this.history.push(run);
    this.trimHistory();
    if (this.followLatestTool || this.selectedToolId === null) {
      this.selectedToolId = run.id;
    }
  }

  private finishToolRun(name: string, output?: string, toolCallId?: string): void {
    const run = [...this.history]
      .reverse()
      .find((entry): entry is ToolCall => entry.kind === "tool"
        && entry.status === "running"
        && (toolCallId ? entry.toolCallId === toolCallId : entry.name === name));
    if (!run) {
      return;
    }
    run.status = "done";
    run.output = output || "";
    if (this.followLatestTool || this.selectedToolId === run.id || this.selectedToolId === null) {
      this.selectedToolId = run.id;
    }
  }

  private trimHistory(): void {
    const maxEntries = 160;
    if (this.history.length <= maxEntries) {
      return;
    }
    const removed = this.history.splice(0, this.history.length - maxEntries);
    if (this.selectedToolId !== null && removed.some((entry) => entry.kind === "tool" && entry.id === this.selectedToolId)) {
      this.selectedToolId = this.getSelectableTools()[0]?.id ?? null;
    }
  }

  private getSelectableTools(): ToolCall[] {
    return this.history.filter((entry): entry is ToolCall => entry.kind === "tool");
  }

  private moveToolSelection(delta: number): void {
    const tools = this.getSelectableTools();
    if (tools.length === 0) {
      return;
    }
    const currentIndex = tools.findIndex((tool) => tool.id === this.selectedToolId);
    const nextIndex = currentIndex === -1
      ? (delta > 0 ? 0 : tools.length - 1)
      : Math.max(0, Math.min(tools.length - 1, currentIndex + delta));
    this.selectedToolId = tools[nextIndex]?.id ?? null;
    this.followLatestTool = nextIndex === tools.length - 1;
    this.ensureSelectedToolVisible();
    this.render();
  }

  private toggleSelectedTool(): void {
    const tool = this.history.find((entry): entry is ToolCall => entry.kind === "tool" && entry.id === this.selectedToolId);
    if (!tool) {
      return;
    }
    tool.expanded = !tool.expanded;
    this.ensureSelectedToolVisible();
    this.render();
  }

  private ensureSelectedToolVisible(): void {
    if (this.selectedToolId === null) {
      return;
    }

    const width = process.stdout.columns || 120;
    const { transcriptHeight } = this.getTranscriptLayout(width);
    const range = this.getToolLineRange(this.selectedToolId, width);
    if (!range) {
      return;
    }

    const blocks = this.buildTranscriptBlocks(width);
    const maxOffset = Math.max(0, blocks.length - transcriptHeight);
    const visibleStart = Math.max(0, blocks.length - transcriptHeight - this.scrollOffset);
    const visibleEnd = visibleStart + transcriptHeight - 1;

    let nextOffset = this.scrollOffset;
    if (range.start < visibleStart) {
      nextOffset = Math.max(0, blocks.length - transcriptHeight - range.start);
    } else if (range.end > visibleEnd) {
      nextOffset = Math.max(0, blocks.length - transcriptHeight - range.end);
    }

    this.scrollOffset = Math.max(0, Math.min(maxOffset, nextOffset));
  }

  private getToolLineRange(toolId: number, width: number): { start: number; end: number } | null {
    let cursor = 0;
    for (const entry of this.history) {
      const entryLines = this.renderHistoryEntry(entry, width);
      const start = cursor;
      const end = cursor + Math.max(0, entryLines.length - 1);
      if (entry.kind === "tool" && entry.id === toolId) {
        return { start, end };
      }
      cursor += entryLines.length + 1;
    }
    return null;
  }

  private getTranscriptLayout(width: number): { transcriptHeight: number } {
    const height = process.stdout.rows || 40;
    const headerHeight = this.renderHeader(width).length;
    const composerHeight = this.renderComposer(width).length;
    const footerHeight = this.renderFooter(width).length;
    return {
      transcriptHeight: Math.max(8, height - headerHeight - composerHeight - footerHeight),
    };
  }

  // ── Session picker helpers ────────────────────────────────

  private moveSessionPicker(delta: number): void {
    if (this.sessionThreads.length === 0) return;
    this.sessionPickerIndex = Math.max(
      0,
      Math.min(this.sessionThreads.length - 1, this.sessionPickerIndex + delta),
    );
    this.render();
  }

  private confirmSessionPicker(): void {
    if (this.sessionThreads.length === 0) return;
    const selected = this.sessionThreads[this.sessionPickerIndex];
    if (!selected) return;
    this.sendBackend({ type: "resume_thread", thread_id: selected.thread_id });
  }

  private renderSessionPicker(width: number, maxHeight: number): string[] {
    const lines: string[] = [];
    lines.push("");
    lines.push(`${COLOR.accent}${COLOR.bold}  📋 恢复历史会话${COLOR.reset}`);
    lines.push("");

    if (this.sessionThreads.length === 0) {
      lines.push(`${COLOR.secondary}  加载中...${COLOR.reset}`);
      while (lines.length < maxHeight) lines.push("");
      return lines;
    }

    const idWidth = 12;
    const previewWidth = Math.max(12, width - idWidth - 14);

    // Reserve 3 lines for header, rest for items
    const visibleItems = Math.max(1, maxHeight - 3);

    // Clamp scroll so selected item is always visible
    if (this.sessionPickerIndex < this.sessionPickerScroll) {
      this.sessionPickerScroll = this.sessionPickerIndex;
    } else if (this.sessionPickerIndex >= this.sessionPickerScroll + visibleItems) {
      this.sessionPickerScroll = this.sessionPickerIndex - visibleItems + 1;
    }

    const end = Math.min(this.sessionThreads.length, this.sessionPickerScroll + visibleItems);
    for (let i = this.sessionPickerScroll; i < end; i++) {
      const t = this.sessionThreads[i];
      const selected = i === this.sessionPickerIndex;
      const id = this.truncate(t.thread_id.slice(-idWidth), idWidth);
      const preview = this.truncate(t.preview || "(empty)", previewWidth);
      const count = `${t.message_count} msgs`;

      if (selected) {
        const content = `${COLOR.selectedText}${COLOR.bold}${id}${COLOR.reset}  ${COLOR.selectedText}${preview}${COLOR.reset}  ${COLOR.selectedSubtle}${count}${COLOR.reset}`;
        lines.push(this.renderSelectedRow(content, width, "▸"));
      } else {
        const idCol = `${COLOR.secondary}${id}${COLOR.reset}`;
        const countCol = `${COLOR.dim}${count}${COLOR.reset}`;
        const previewCol = `${COLOR.soft}${preview}${COLOR.reset}`;
        lines.push(`   ${idCol}  ${previewCol}  ${countCol}`);
      }
    }

    // Show scroll hint if there are more items
    if (this.sessionThreads.length > visibleItems) {
      const remaining = this.sessionThreads.length - end;
      if (remaining > 0) {
        lines.push(`${COLOR.dim}   ... 还有 ${remaining} 个会话${COLOR.reset}`);
      }
    }

    while (lines.length < maxHeight) lines.push("");
    return lines;
  }

  // ── Question mode ─────────────────────────────────────────

  private handleQuestionKeypress(key: readline.Key): void {
    const question = this.activeQuestions[this.currentQuestionIndex];
    if (!question) return;

    const options = question.options || [];

    // ── Freeform text mode (no options) ──
    if (options.length === 0) {
      if (key.name === "return") {
        this.submitQuestionAnswer(this.otherText.trim() ? [this.otherText.trim()] : []);
        return;
      }
      if (key.name === "escape") {
        this.submitQuestionAnswer([]);
        return;
      }
      if (key.name === "backspace") {
        this.otherText = this.otherText.slice(0, -1);
        this.render();
        return;
      }
      if (typeof key.sequence === "string" && key.sequence >= " ") {
        this.otherText += key.sequence;
        this.render();
      }
      return;
    }

    const totalSlots = options.length + 1; // +1 for "Other"

    // ── "Other" text input mode ──
    if (this.otherMode) {
      if (key.name === "escape") {
        this.otherMode = false;
        this.otherText = "";
        this.render();
        return;
      }
      if (key.name === "return") {
        if (this.otherText.trim()) {
          this.submitQuestionAnswer([this.otherText.trim()]);
        }
        return;
      }
      if (key.name === "up") {
        this.otherMode = false;
        this.optionIndex = totalSlots - 1;
        this.render();
        return;
      }
      if (key.name === "backspace") {
        this.otherText = this.otherText.slice(0, -1);
        this.render();
        return;
      }
      if (typeof key.sequence === "string" && key.sequence >= " ") {
        this.otherText += key.sequence;
        this.render();
      }
      return;
    }

    // ── Option navigation mode ──
    if (key.name === "up") {
      this.optionIndex = Math.max(0, this.optionIndex - 1);
      this.render();
      return;
    }
    if (key.name === "down") {
      this.optionIndex = Math.min(totalSlots - 1, this.optionIndex + 1);
      this.render();
      return;
    }

    if (key.name === "return" || key.name === " ") {
      const isOther = this.optionIndex === options.length;

      if (isOther) {
        this.otherMode = true;
        this.otherText = "";
        this.render();
        return;
      }

      const selectedOpt = options[this.optionIndex];
      if (!selectedOpt) return;

      if (question.multiSelect) {
        if (this.multiSelected.has(this.optionIndex)) {
          this.multiSelected.delete(this.optionIndex);
        } else {
          this.multiSelected.add(this.optionIndex);
        }
        this.render();
        return;
      }

      // Single-select: auto-submit
      this.submitQuestionAnswer([selectedOpt.label]);
      return;
    }

    if (key.name === "tab" && question.multiSelect && this.multiSelected.size > 0) {
      const selected = Array.from(this.multiSelected)
        .sort((a, b) => a - b)
        .map((i) => options[i].label);
      this.submitQuestionAnswer(selected);
      return;
    }

    if (key.name === "escape") {
      this.submitQuestionAnswer([]);
    }
  }

  private submitQuestionAnswer(selected: string[]): void {
    this.questionAnswers.push({
      question_index: this.currentQuestionIndex,
      selected,
    });

    if (this.currentQuestionIndex + 1 < this.activeQuestions.length) {
      this.currentQuestionIndex++;
      this.optionIndex = 0;
      this.multiSelected = new Set();
      this.otherMode = false;
      this.otherText = "";
      this.render();
      return;
    }

    // All questions answered — format as user message and send as prompt
    const answerText = this.formatQuestionAnswer();
    this.questionMode = false;
    this.activeQuestions = [];
    this.questionAnswers = [];

    // Push to history and dispatch as a normal prompt
    const messageId = this.pushHistory({
      kind: "message",
      role: "user",
      content: answerText,
      state: this.generating ? "queued" : "sent",
    });
    if (this.generating) {
      this.pendingPrompts.push({ messageId, text: answerText });
    } else {
      this.dispatchPrompt(answerText, messageId);
    }
    this.render();
  }

  private formatQuestionAnswer(): string {
    if (this.questionAnswers.length === 0) {
      return "(跳过了所有问题)";
    }
    const parts: string[] = [];
    for (const ans of this.questionAnswers) {
      const q = this.activeQuestions[ans.question_index];
      if (!q) continue;
      const questionText = q.question;
      const answer = ans.selected.length > 0 ? ans.selected.join(", ") : "(跳过)";
      parts.push(`${questionText} → ${answer}`);
    }
    return parts.join("\n");
  }

  private renderQuestionUI(width: number, maxHeight: number): string[] {
    const lines: string[] = [];
    const question = this.activeQuestions[this.currentQuestionIndex];

    if (!question) {
      lines.push(`${COLOR.secondary}  No questions.${COLOR.reset}`);
      while (lines.length < maxHeight) lines.push("");
      return lines;
    }

    const options = question.options || [];

    // Progress indicator for multi-question
    const progress =
      this.activeQuestions.length > 1
        ? ` (${this.currentQuestionIndex + 1}/${this.activeQuestions.length})`
        : "";
    const headerBadge = question.header ? ` [${question.header}]` : "";

    lines.push("");
    lines.push(`${COLOR.accent}${COLOR.bold}  ?${progress}${headerBadge}${COLOR.reset}`);
    lines.push(`${COLOR.bold}  ${question.question}${COLOR.reset}`);
    lines.push("");

    // ── Freeform text mode (no options) ──
    if (options.length === 0) {
      const inputLine = ` > ${this.otherText}_`;
      lines.push(this.renderSelectedRow(`${COLOR.selectedText}${COLOR.bold}${inputLine}${COLOR.reset}`, width));
      while (lines.length < maxHeight) lines.push("");
      return lines;
    }

    // ── Options list ──
    for (let i = 0; i < options.length; i++) {
      const opt = options[i];
      const highlighted = i === this.optionIndex;
      const multiChecked = this.multiSelected.has(i);

      if (question.multiSelect) {
        const check = multiChecked ? "[x]" : "[ ]";
        const label = `${check} ${opt.label}`;
        const desc = opt.description ? ` - ${opt.description}` : "";
        if (highlighted) {
          const content = `${COLOR.selectedText}${COLOR.bold}${label}${COLOR.reset}${COLOR.selectedSubtle}${desc}${COLOR.reset}`;
          lines.push(this.renderSelectedRow(content, width));
        } else {
          lines.push(`   ${COLOR.soft}${label}${COLOR.reset}${COLOR.dim}${desc}${COLOR.reset}`);
        }
      } else {
        const desc = opt.description ? ` - ${opt.description}` : "";
        if (highlighted) {
          const content = `${COLOR.selectedText}${COLOR.bold}${opt.label}${COLOR.reset}${COLOR.selectedSubtle}${desc}${COLOR.reset}`;
          lines.push(this.renderSelectedRow(content, width));
        } else {
          lines.push(`   ${COLOR.soft}${opt.label}${COLOR.reset}${COLOR.dim}${desc}${COLOR.reset}`);
        }
      }
    }

    // ── "Other" option ──
    const isOtherHighlighted = this.optionIndex === options.length;
    if (this.otherMode) {
      const inputLine = ` > Other: ${this.otherText}_`;
      lines.push(this.renderSelectedRow(`${COLOR.warning}${COLOR.bold}${inputLine}${COLOR.reset}`, width));
    } else if (isOtherHighlighted) {
      const content = `${COLOR.selectedText}${COLOR.bold}Other${COLOR.reset}${COLOR.selectedSubtle} (自定义输入)...${COLOR.reset}`;
      lines.push(this.renderSelectedRow(content, width));
    } else {
      lines.push(`   ${COLOR.secondary}Other (自定义输入)...${COLOR.reset}`);
    }

    while (lines.length < maxHeight) lines.push("");
    return lines;
  }

  private getQuestionKeybindingHint(): string {
    const question = this.activeQuestions[this.currentQuestionIndex];
    if (!question) return "";
    if (this.otherMode || !question.options?.length) return "Enter 确认  Esc 取消";

    const parts = ["↑↓ 选择"];
    if (question.multiSelect) {
      parts.push("Space 切换");
      parts.push("Tab 提交");
    } else {
      parts.push("Enter 确认");
    }
    parts.push("Esc 跳过");
    return parts.join("  ");
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

    if (this.showSessionPicker) {
      const picker = this.renderSessionPicker(width, Math.max(8, height - header.length - 4));
      const footer = [
        "",
        `${COLOR.secondary}↑↓ 选择  Enter 恢复  Esc 新会话${COLOR.reset}`,
      ];
      const frameLines = [...header, ...picker, ...footer];
      const frame = frameLines.join("\n");
      if (frame !== this.lastFrame) {
        process.stdout.write("\x1b[H\x1b[2J");
        process.stdout.write(frame);
        this.lastFrame = frame;
      }
      return;
    }

    if (this.questionMode) {
      const questionUI = this.renderQuestionUI(width, Math.max(8, height - header.length - 4));
      const footer = [
        "",
        `${COLOR.secondary}${this.getQuestionKeybindingHint()}${COLOR.reset}`,
      ];
      const frameLines = [...header, ...questionUI, ...footer];
      const frame = frameLines.join("\n");
      if (frame !== this.lastFrame) {
        process.stdout.write("\x1b[H\x1b[2J");
        process.stdout.write(frame);
        this.lastFrame = frame;
      }
      return;
    }

    const composer = this.renderComposer(width);
    const footer = this.renderFooter(width);
    const reserved = header.length + composer.length + footer.length;
    const transcriptHeight = Math.max(8, height - reserved);
    const transcript = this.renderTranscript(width, transcriptHeight, header.length);
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
    const meta = `${this.model} · thread ${this.threadId.slice(-8) || "--------"}`;
    const cwd = this.truncate(this.cwd, Math.max(20, width - 18));
    const logo = [
      "█▄  █  ▄██▄",
      "█ ▀ █  █  █",
      "▀   ▀  ▀██▀",
    ];
    return [
      `${COLOR.accent}${COLOR.bold}${logo[0]}${COLOR.reset}  ${COLOR.secondary}${this.truncate(meta, Math.max(12, width - 16))}${COLOR.reset}`,
      `${COLOR.accent}${COLOR.bold}${logo[1]}${COLOR.reset}  ${COLOR.secondary}${cwd}${COLOR.reset}`,
      `${COLOR.accent}${COLOR.bold}${logo[2]}${COLOR.reset}`,
    ];
  }

  private renderTranscript(width: number, height: number, headerHeight: number): string[] {
    const blocks = this.buildTranscriptBlocks(width);
    const maxOffset = Math.max(0, blocks.length - height);
    this.scrollOffset = Math.max(0, Math.min(this.scrollOffset, maxOffset));
    const start = Math.max(0, blocks.length - height - this.scrollOffset);
    const visible = blocks.slice(start, start + height);
    void headerHeight;
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
      lines.push(...this.renderHistoryEntry(message, width));
      lines.push("");
    }

    if (this.streaming || this.generating) {
      lines.push(...this.renderHistoryEntry({
        id: -1,
        kind: "message",
        role: "assistant",
        content: this.streaming || "思考中...",
      }, width));
      lines.push("");
    }

    while (lines.length > 0 && !lines[lines.length - 1].trim()) {
      lines.pop();
    }

    return lines;
  }

  private renderHistoryEntry(entry: Message, width: number): string[] {
    if (entry.kind === "tool") {
      return this.renderToolBlock(entry, width);
    }
    return this.renderMessageBlock(entry, width);
  }

  private renderMessageBlock(message: TextMessage, width: number): string[] {
    const { role, content, state } = message;
    const availableWidth = Math.max(12, width - 4);
    const prefix = role === "user" ? "❯ " : role === "assistant" ? "⏺ " : "  ";
    const continuation = "  ";

    if (role === "assistant") {
      const renderedLines = this.renderMarkdownLines(content || " ", availableWidth);
      return renderedLines.map((line, index) => {
        const leader = index === 0 ? prefix : continuation;
        const marker = `${COLOR.accent}${leader}${COLOR.reset}`;
        return `${marker}${line}`;
      });
    }

    const wrapped = this.wrap(content || " ", availableWidth);
    return wrapped.map((line, index) => {
      const leader = index === 0 ? prefix : continuation;
      const contentWithState = role === "user" && index === 0
        ? this.renderUserStateTag(line, state)
        : line;
      const body = role === "user"
        ? `${COLOR.bold}${contentWithState}${COLOR.reset}`
        : `${COLOR.secondary}${line}${COLOR.reset}`;
      const marker = role === "user"
        ? `${COLOR.user}${COLOR.bold}${leader}${COLOR.reset}`
        : `${COLOR.secondary}${leader}${COLOR.reset}`;
      return `${marker}${body}`;
    });
  }

  // ── Markdown → ANSI renderer ──────────────────────────────────────
  // Processes markdown source and returns lines of ANSI-styled text,
  // each line already wrapped to `width`.

  private renderMarkdownLines(content: string, width: number): string[] {
    const lines: string[] = [];
    const sourceLines = content.split("\n");
    let i = 0;

    while (i < sourceLines.length) {
      const raw = sourceLines[i];

      // ── Fenced code block ────────────────────────────────────
      const fenceMatch = raw.match(/^(\s*)```/);
      if (fenceMatch) {
        const fenceIndent = fenceMatch[1];
        const codeLines: string[] = [];
        i++;
        while (i < sourceLines.length && !sourceLines[i].match(/^\s*```/)) {
          codeLines.push(sourceLines[i]);
          i++;
        }
        i++; // skip closing ```
        for (const cl of codeLines) {
          const indented = fenceIndent + "  " + cl;
          lines.push(`${COLOR.md.codeBg}${COLOR.md.code}${this.padRight(indented, width)}${COLOR.reset}`);
        }
        if (codeLines.length === 0) {
          lines.push(`${COLOR.md.codeBg}${COLOR.md.code}${this.padRight(fenceIndent + "  ", width)}${COLOR.reset}`);
        }
        continue;
      }

      // ── Table ────────────────────────────────────────────────
      if (raw.includes("|") && raw.trim().startsWith("|")) {
        const tableBlock: string[] = [];
        while (i < sourceLines.length && sourceLines[i].includes("|") && sourceLines[i].trim().startsWith("|")) {
          tableBlock.push(sourceLines[i]);
          i++;
        }
        lines.push(...this.renderMarkdownTable(tableBlock, width));
        continue;
      }

      // ── Heading ──────────────────────────────────────────────
      const headingMatch = raw.match(/^(#{1,6})\s+(.*)/);
      if (headingMatch) {
        const level = headingMatch[1].length;
        const text = this.renderInlineMarkdown(headingMatch[2]);
        const prefix = "▎" + " ".repeat(Math.max(0, 4 - level));
        lines.push("");
        lines.push(`${COLOR.md.headingBold}${prefix}${text}${COLOR.reset}`);
        lines.push("");
        i++;
        continue;
      }

      // ── Horizontal rule ──────────────────────────────────────
      if (/^(\s*[-*_]){3,}\s*$/.test(raw)) {
        lines.push(`${COLOR.md.hr}${"─".repeat(width)}${COLOR.reset}`);
        i++;
        continue;
      }

      // ── Blockquote ───────────────────────────────────────────
      if (raw.startsWith(">")) {
        const quoteLines: string[] = [];
        while (i < sourceLines.length && sourceLines[i].startsWith(">")) {
          quoteLines.push(sourceLines[i].replace(/^>\s?/, ""));
          i++;
        }
        for (const ql of quoteLines) {
          const styled = this.renderInlineMarkdown(ql);
          const wrapped = this.wrapAnsiAware(styled, width - 2);
          for (const wl of wrapped) {
            lines.push(`${COLOR.md.blockquote}▎ ${wl}${COLOR.reset}`);
          }
        }
        continue;
      }

      // ── Unordered list ───────────────────────────────────────
      if (/^\s*[-*+]\s/.test(raw)) {
        while (i < sourceLines.length && /^\s*[-*+]\s/.test(sourceLines[i])) {
          const itemText = sourceLines[i].replace(/^\s*[-*+]\s/, "");
          const styled = this.renderInlineMarkdown(itemText);
          const wrapped = this.wrapAnsiAware(styled, width - 2);
          for (let wi = 0; wi < wrapped.length; wi++) {
            const bullet = wi === 0 ? `${COLOR.md.listBullet}• ${COLOR.reset}` : "  ";
            lines.push(`${bullet}${wrapped[wi]}`);
          }
          i++;
        }
        continue;
      }

      // ── Ordered list ─────────────────────────────────────────
      if (/^\s*\d+\.\s/.test(raw)) {
        let num = 1;
        while (i < sourceLines.length && /^\s*\d+\.\s/.test(sourceLines[i])) {
          const itemText = sourceLines[i].replace(/^\s*\d+\.\s/, "");
          const styled = this.renderInlineMarkdown(itemText);
          const wrapped = this.wrapAnsiAware(styled, width - 4);
          for (let wi = 0; wi < wrapped.length; wi++) {
            const bullet = wi === 0
              ? `${COLOR.md.listBullet}${String(num).padStart(2)}. ${COLOR.reset}`
              : "    ";
            lines.push(`${bullet}${wrapped[wi]}`);
          }
          num++;
          i++;
        }
        continue;
      }

      // ── Empty line ───────────────────────────────────────────
      if (!raw.trim()) {
        lines.push("");
        i++;
        continue;
      }

      // ── Paragraph (default) ──────────────────────────────────
      const styled = this.renderInlineMarkdown(raw);
      const wrapped = this.wrapAnsiAware(styled, width);
      lines.push(...wrapped);
      i++;
    }

    return lines;
  }

  /** Render inline markdown: **bold**, *italic*, `code`, ~~strike~~, [link](url) */
  private renderInlineMarkdown(text: string): string {
    // Escape sequences we insert use \x00 markers to avoid double-processing
    let result = text;

    // Inline code: `code`
    result = result.replace(/`([^`]+)`/g, (_, code) =>
      `\x00CODE_START\x00${code}\x00CODE_END\x00`);

    // Bold + italic: ***text***
    result = result.replace(/\*\*\*(.+?)\*\*\*/g, (_, t) =>
      `\x00BI_START\x00${t}\x00BI_END\x00`);

    // Bold: **text**
    result = result.replace(/\*\*(.+?)\*\*/g, (_, t) =>
      `\x00B_START\x00${t}\x00B_END\x00`);

    // Italic: *text*
    result = result.replace(/(?<!\*)\*([^*]+?)\*(?!\*)/g, (_, t) =>
      `\x00I_START\x00${t}\x00I_END\x00`);

    // Strikethrough: ~~text~~
    result = result.replace(/~~(.+?)~~/g, (_, t) =>
      `\x00S_START\x00${t}\x00S_END\x00`);

    // Links: [text](url)
    result = result.replace(/\[([^\]]+)\]\(([^)]+)\)/g, (_, linkText, url) =>
      `\x00LINK_START\x00${linkText}\x00LINK_MID\x00${url}\x00LINK_END\x00`);

    // Replace markers with ANSI
    result = result
      .replace(/\x00CODE_START\x00/g, `${COLOR.md.codeBg}${COLOR.md.code}`)
      .replace(/\x00CODE_END\x00/g, COLOR.reset)
      .replace(/\x00BI_START\x00/g, `${COLOR.md.strong}${COLOR.italic}`)
      .replace(/\x00BI_END\x00/g, COLOR.reset)
      .replace(/\x00B_START\x00/g, COLOR.md.strong)
      .replace(/\x00B_END\x00/g, COLOR.reset)
      .replace(/\x00I_START\x00/g, COLOR.italic)
      .replace(/\x00I_END\x00/g, COLOR.reset)
      .replace(/\x00S_START\x00/g, COLOR.strikethrough)
      .replace(/\x00S_END\x00/g, COLOR.reset)
      .replace(/\x00LINK_START\x00/g, COLOR.md.link)
      .replace(/\x00LINK_MID\x00/g, `${COLOR.reset} `)
      .replace(/\x00LINK_END\x00/g, COLOR.reset);

    return `${COLOR.soft}${result}${COLOR.reset}`;
  }

  /** Render a markdown table block into ANSI-styled lines */
  private renderMarkdownTable(tableRows: string[], _width: number): string[] {
    if (tableRows.length === 0) return [];

    const parsedRows: string[][] = [];
    for (const row of tableRows) {
      // Skip separator rows like |---|---|
      if (/^\|[\s\-:|]+\|$/.test(row.trim())) continue;
      const cells = row.split("|").slice(1, -1).map((c) => c.trim());
      parsedRows.push(cells);
    }

    if (parsedRows.length === 0) return [];

    // Calculate column widths
    const colCount = Math.max(...parsedRows.map((r) => r.length));
    const colWidths: number[] = [];
    for (let c = 0; c < colCount; c++) {
      let maxW = 0;
      for (const row of parsedRows) {
        const cell = row[c] || "";
        maxW = Math.max(maxW, this.visibleLength(this.stripAnsi(cell)));
      }
      colWidths.push(maxW);
    }

    const lines: string[] = [];
    const b = COLOR.md.tableBorder;
    const r = COLOR.reset;

    for (let ri = 0; ri < parsedRows.length; ri++) {
      const row = parsedRows[ri];
      const styledCells: string[] = [];
      for (let c = 0; c < colCount; c++) {
        const cell = row[c] || "";
        const isHeader = ri === 0;
        const styled = isHeader ? `${COLOR.md.tableHeader}${cell}${COLOR.reset}` : `${COLOR.soft}${cell}${COLOR.reset}`;
        const plainLen = this.visibleLength(cell);
        const pad = " ".repeat(Math.max(0, colWidths[c] - plainLen));
        styledCells.push(` ${styled}${pad} `);
      }
      lines.push(`${b}│${r}${styledCells.join(`${b}│${r}`)}${b}│${r}`);

      if (ri === 0) {
        const sep = colWidths.map((w) => `${b}${"─".repeat(w + 2)}${r}`);
        lines.push(`${b}├${r}${sep.join(`${b}┼${r}`)}${b}┤${r}`);
      }
    }

    return lines;
  }

  /** Wrap text that may contain ANSI escape sequences, preserving them across line breaks */
  private wrapAnsiAware(text: string, width: number): string[] {
    return text.split("\n").flatMap((line) => {
      if (!line || this.visibleLength(line) <= width) return [line];

      const parts: string[] = [];
      let remaining = line;
      while (this.visibleLength(remaining) > width) {
        const [chunk, rest] = this.sliceByWidthAnsi(remaining, width);
        parts.push(chunk);
        remaining = rest;
      }
      parts.push(remaining);
      return parts;
    });
  }

  /** Slice text by visible width while keeping ANSI sequences intact.
   *  Returns [slicedPart, remaining]. */
  private sliceByWidthAnsi(text: string, width: number): [string, string] {
    let result = "";
    let consumed = 0;
    let visiblePos = 0;
    let activeStyles = "";

    const ansiRegex = /\x1b\[[0-9;]*m/g;
    let match: RegExpExecArray | null;
    let lastIndex = 0;

    // Collect all ANSI sequences with their positions
    const sequences: { index: number; length: number; code: string }[] = [];
    ansiRegex.lastIndex = 0;
    while ((match = ansiRegex.exec(text)) !== null) {
      sequences.push({ index: match.index, length: match[0].length, code: match[0] });
    }

    let seqIndex = 0;
    for (const char of Array.from(text)) {
      const charPos = text.indexOf(char, lastIndex);

      // Process any ANSI sequences before this character
      while (seqIndex < sequences.length && sequences[seqIndex].index < charPos + char.length) {
        const seq = sequences[seqIndex];
        if (seq.index >= lastIndex && seq.index <= charPos) {
          activeStyles += seq.code;
          seqIndex++;
        } else {
          break;
        }
      }

      const cw = this.charWidth(char);
      if (visiblePos + cw > width) {
        // Emit a reset at the end of this line and prepend active styles to next line
        return [result + COLOR.reset, activeStyles + text.slice(charPos)];
      }

      result += char;
      visiblePos += cw;
      lastIndex = charPos + char.length;
    }

    return [result, ""];
  }

  private padRight(text: string, width: number): string {
    const visible = this.visibleLength(text);
    if (visible >= width) return text;
    return text + " ".repeat(width - visible);
  }

  private renderSelectedRow(content: string, width: number, marker = "›"): string {
    const inner = `${COLOR.selectedBorder}${COLOR.bold}${marker} ${COLOR.reset}${content}`;
    return `${COLOR.selectedBg}${this.padRight(inner, width)}${COLOR.reset}`;
  }

  // ── End Markdown renderer ─────────────────────────────────────

  private renderToolBlock(tool: ToolCall, width: number): string[] {
    const lines: string[] = [];
    const selected = tool.id === this.selectedToolId;
    const prefix = `${selected ? `${COLOR.selectedBorder}${COLOR.bold}` : `${COLOR.accent}`}${selected ? "▸" : "⏺"} ${COLOR.reset}`;
    const bodyWidth = Math.max(12, width - 2);
    const summary = this.formatToolSummary(tool, bodyWidth);

    for (const line of this.wrapAnsiAware(summary, bodyWidth)) {
      const composed = `${prefix}${selected ? `${COLOR.selectedText}${line}${COLOR.reset}` : line}`;
      lines.push(selected ? `${COLOR.selectedBg}${this.padRight(composed, width)}${COLOR.reset}` : composed);
    }

    if (tool.expanded) {
      lines.push(...this.renderExpandedTool(tool, width));
    }
    return lines;
  }

  private renderExpandedTool(tool: ToolCall, width: number): string[] {
    const lines: string[] = [];
    const availableWidth = Math.max(12, width - 6);
    const args = tool.args && Object.keys(tool.args).length > 0
      ? this.formatToolArgs(tool.args)
      : "无参数";
    const output = tool.output?.trim() ? tool.output.trim() : "(无输出)";

    for (const line of this.wrap(`args: ${args}`, availableWidth)) {
      lines.push(`${COLOR.dim}  ⎿ ${line}${COLOR.reset}`);
    }
    for (const line of this.wrap(`result: ${output}`, availableWidth)) {
      lines.push(`${COLOR.dim}  ⎿ ${line}${COLOR.reset}`);
    }
    return lines;
  }

  private formatToolSummary(tool: ToolCall, width: number): string {
    const title = this.describeTool(tool);
    const status = tool.status === "running"
      ? `${COLOR.warning}执行中...${COLOR.reset}`
      : `${COLOR.secondary}${this.describeToolOutcome(tool, width)}${COLOR.reset}`;
    return this.truncateAnsiAware(`${title}${tool.status === "done" ? `  ${status}` : `  ${status}`}`, width);
  }

  private describeTool(tool: ToolCall): string {
    const argSummary = this.describeToolArgs(tool);
    if (!argSummary) {
      return tool.name;
    }
    return `${tool.name} ${argSummary}`;
  }

  private describeToolArgs(tool: ToolCall): string {
    if (!tool.args || Object.keys(tool.args).length === 0) {
      return "";
    }
    if (tool.name === "ask_user_question") {
      const questions = Array.isArray(tool.args.questions) ? tool.args.questions.length : 0;
      return `提出 ${questions || 1} 个问题`;
    }
    return `(${this.formatToolArgs(tool.args)})`;
  }

  private describeToolOutcome(tool: ToolCall, width: number): string {
    const output = tool.output?.trim() ?? "";
    if (!output) {
      return "已完成";
    }
    const singleLine = output.replace(/\s+/g, " ").trim();
    const compact = this.truncate(singleLine, Math.max(12, width - 12));
    if (singleLine.length <= compact.length) {
      return compact;
    }
    const hiddenLines = Math.max(1, output.split("\n").length - 3);
    return `${compact} (ctrl+o 展开, 约 +${hiddenLines} 行)`;
  }

  private formatToolArgs(args: Record<string, unknown>): string {
    return Object.entries(args)
      .map(([key, value]) => `${key}=${this.formatValue(value)}`)
      .join(", ");
  }

  private formatValue(value: unknown): string {
    if (typeof value === "string") {
      return JSON.stringify(this.truncate(value, 40));
    }
    if (Array.isArray(value)) {
      return `[${value.map((item) => this.formatValue(item)).join(", ")}]`;
    }
    if (value && typeof value === "object") {
      try {
        return this.truncate(JSON.stringify(value), 64);
      } catch {
        return "{...}";
      }
    }
    return String(value);
  }

  private summarizeToolOutput(output: string): string {
    const compact = output.replace(/\s+/g, " ").trim();
    return this.truncate(compact || "(empty)", 56);
  }

  private renderUserStateTag(line: string, state?: "queued" | "sent"): string {
    if (state === "queued") {
      return `${line} ${COLOR.warning}[queued]${COLOR.reset}${COLOR.bold}`;
    }
    return line;
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
    const queue = this.pendingPrompts.length > 0 ? `  queue ${this.pendingPrompts.length}` : "";
    const scroll = this.scrollOffset > 0 ? `  scroll +${this.scrollOffset}` : "";
    const text = `Enter submit  Shift+Enter newline  wheel/PgUp/PgDn scroll  Ctrl+J/K tool  ${state}${queue}  ${this.subagentModel}${scroll}`;
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

  private truncateAnsiAware(text: string, width: number): string {
    if (this.visibleLength(text) <= width) {
      return text;
    }
    if (width <= 0) {
      return "";
    }

    let result = "";
    let visible = 0;
    const ansiPattern = /\x1b\[[0-9;]*m/g;
    let index = 0;

    while (index < text.length && visible < Math.max(0, width - 1)) {
      ansiPattern.lastIndex = index;
      const ansiMatch = ansiPattern.exec(text);
      if (ansiMatch && ansiMatch.index === index) {
        result += ansiMatch[0];
        index += ansiMatch[0].length;
        continue;
      }

      const char = Array.from(text.slice(index))[0];
      if (!char) {
        break;
      }
      const charWidth = this.charWidth(char);
      if (visible + charWidth > Math.max(0, width - 1)) {
        break;
      }
      result += char;
      visible += charWidth;
      index += char.length;
    }

    return `${result}…${COLOR.reset}`;
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
    // 禁用鼠标上报，保留终端原生文本选区与复制行为。
    // 同时启用扩展键盘协议，让 Shift+Enter 等组合键能与普通 Enter 区分。
    process.stdout.write(`\x1b[?1049h\x1b[?25h${ENABLE_KITTY_KEYBOARD}${ENABLE_MODIFY_OTHER_KEYS}`);
  }

  private scrollTranscript(delta: number): void {
    const width = process.stdout.columns || 120;
    const { transcriptHeight } = this.getTranscriptLayout(width);
    const maxOffset = Math.max(0, this.buildTranscriptBlocks(width).length - transcriptHeight);
    const nextOffset = Math.max(0, Math.min(maxOffset, this.scrollOffset + delta));
    if (nextOffset !== this.scrollOffset) {
      this.scrollOffset = nextOffset;
      this.render();
    }
  }

  private positionCursor(width: number, promptStartRow: number): void {
    const availableWidth = Math.max(12, width - 4);
    // composer 的第 1 行是分隔线，真正输入内容从下一行开始。
    let visualRowOffset = 2;

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
    // `❯ ` 和续行前缀 `  ` 都占 2 列，光标应落在正文起始列。
    const promptPrefix = 3;

    const row = promptStartRow + visualRowOffset + cursorLine;
    const col = promptPrefix + cursorColBase;
    process.stdout.write(`\x1b[${row};${Math.max(1, col)}H`);
  }

  private shutdown(): void {
    process.stdout.write(`${DISABLE_MODIFY_OTHER_KEYS}${DISABLE_KITTY_KEYBOARD}\x1b[?25h\x1b[?1049l`);
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
