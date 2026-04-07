// SGR 鼠标事件正则：CSI < button ; col ; row M/m
export const SGR_MOUSE_RE = /^\x1b\[<(\d+);(\d+);(\d+)([Mm])$/;
const SGR_MOUSE_PREFIX_RE = /^\x1b\[<\d*;?\d*;?\d*([Mm]?)$/;

const CTRL_J_SEQUENCES = new Set(["\x0a", "\x1b[106;5u", "\x1b[27;5;106~"]);
const CTRL_K_SEQUENCES = new Set(["\x0b", "\x1b[107;5u", "\x1b[27;5;107~"]);
const CTRL_O_SEQUENCES = new Set(["\x0f", "\x1b[111;5u", "\x1b[27;5;111~"]);
const CTRL_C_SEQUENCES = new Set(["\x03", "\x1b[99;5u", "\x1b[99;6u", "\x1b[27;5;99~", "\x1b[27;6;99~"]);
const ESCAPE_SEQUENCES = new Set(["\x1b", "\x1b[27u", "\x1b[27;1u", "\x1b[27~"]);
const SHIFT_ENTER_SEQUENCES = new Set(["\x1b[13;2u", "\x1b[13;2~", "\x1b[27;2;13~", "\x1b[27;13;2~"]);
const KEYPRESS_PASSTHROUGH_SEQUENCES = new Set([
  "\x1b[A",
  "\x1b[B",
  "\x1b[C",
  "\x1b[D",
  "\x1b[1~",
  "\x1b[4~",
  "\x1b[5~",
  "\x1b[6~",
  "\x1b[H",
  "\x1b[F",
  "\x1bOH",
  "\x1bOF",
]);

const EXACT_CONTROL_SEQUENCES = [
  ...CTRL_J_SEQUENCES,
  ...CTRL_K_SEQUENCES,
  ...CTRL_O_SEQUENCES,
  ...CTRL_C_SEQUENCES,
  ...ESCAPE_SEQUENCES,
  ...SHIFT_ENTER_SEQUENCES,
  ...KEYPRESS_PASSTHROUGH_SEQUENCES,
].sort((left, right) => right.length - left.length);

export type RawInputToken =
  | { kind: "control"; value: string }
  | { kind: "text"; value: string };

export function isCtrlJSequence(chunk: string): boolean {
  return CTRL_J_SEQUENCES.has(chunk);
}

export function isCtrlKSequence(chunk: string): boolean {
  return CTRL_K_SEQUENCES.has(chunk);
}

export function isCtrlOSequence(chunk: string): boolean {
  return CTRL_O_SEQUENCES.has(chunk);
}

export function isCtrlCSequence(chunk: string): boolean {
  return CTRL_C_SEQUENCES.has(chunk);
}

export function isEscapeSequence(chunk: string): boolean {
  return ESCAPE_SEQUENCES.has(chunk);
}

export function isShiftEnterSequence(chunk: string): boolean {
  return SHIFT_ENTER_SEQUENCES.has(chunk);
}

export function isKeypressPassthroughSequence(chunk: string): boolean {
  return KEYPRESS_PASSTHROUGH_SEQUENCES.has(chunk);
}

export function looksLikeMouseSequence(chunk: string): boolean {
  return SGR_MOUSE_RE.test(chunk) || (chunk.length >= 3 && chunk.startsWith("\x1b[M"));
}

export class RawInputParser {
  private buffer = "";

  push(chunk: string): void {
    this.buffer += chunk;
  }

  drain(): RawInputToken[] {
    const tokens: RawInputToken[] = [];
    while (this.buffer.length > 0) {
      const sequence = this.readNextControlSequence();
      if (sequence === null) {
        break;
      }
      if (sequence !== undefined) {
        this.buffer = this.buffer.slice(sequence.length);
        tokens.push({ kind: "control", value: sequence });
        continue;
      }

      const nextEsc = this.buffer.indexOf("\x1b");
      const text = nextEsc === -1 ? this.buffer : this.buffer.slice(0, nextEsc);
      if (!text) {
        break;
      }
      this.buffer = this.buffer.slice(text.length);
      tokens.push({ kind: "text", value: text });
    }
    return tokens;
  }

  hasPendingEscapePrefix(): boolean {
    return this.buffer === "\x1b";
  }

  flushPendingEscape(): RawInputToken[] {
    if (!this.hasPendingEscapePrefix()) {
      return [];
    }
    this.buffer = "";
    return [{ kind: "control", value: "\x1b" }];
  }

  private readNextControlSequence(): string | null | undefined {
    const input = this.buffer;
    if (!input) {
      return undefined;
    }

    if (input.startsWith("\x1b[<")) {
      const mouseEnd = input.match(/^\x1b\[<\d+;\d+;\d+[Mm]/);
      if (mouseEnd) {
        return mouseEnd[0];
      }
      if (SGR_MOUSE_PREFIX_RE.test(input)) {
        return null;
      }
    }

    if (input.startsWith("\x1b[M")) {
      if (input.length >= 6) {
        return input.slice(0, 6);
      }
      return null;
    }

    // 单独的 ESC 既可能是真正的 Escape，也可能是方向键等 CSI 序列的前缀。
    // 这里只在看到后续字节时再立即确认，避免把箭头键拆成 ESC + "[A"。
    if (input === "\x1b") {
      return null;
    }

    for (const seq of EXACT_CONTROL_SEQUENCES) {
      if (input.startsWith(seq)) {
        return seq;
      }
    }

    if (input[0] === "\x1b") {
      const maybePrefix = EXACT_CONTROL_SEQUENCES.some((seq) => seq.startsWith(input));
      if (maybePrefix) {
        return null;
      }
    }

    return undefined;
  }
}
