import { execFile } from "node:child_process";
import process from "node:process";

export const ENABLE_KITTY_KEYBOARD = "\x1b[>1u";
export const DISABLE_KITTY_KEYBOARD = "\x1b[<u";
export const ENABLE_MODIFY_OTHER_KEYS = "\x1b[>4;2m";
export const DISABLE_MODIFY_OTHER_KEYS = "\x1b[>4m";

// 鼠标跟踪：1000 基础按钮/滚轮，1002 拖拽，1003 全移动，1006 SGR 编码
export const ENABLE_MOUSE_TRACKING = "\x1b[?1000h\x1b[?1002h\x1b[?1003h\x1b[?1006h";
export const DISABLE_MOUSE_TRACKING = "\x1b[?1006l\x1b[?1003l\x1b[?1002l\x1b[?1000l";

const WHEEL_ACCEL_WINDOW_MS = 40;
const WHEEL_ACCEL_STEP = 0.3;
const WHEEL_ACCEL_MAX = 6;
const WHEEL_DECAY_HALFLIFE_MS = 150;
const WHEEL_DECAY_STEP = 5;
const WHEEL_BURST_MS = 5;
const WHEEL_DECAY_GAP_MS = 80;
const WHEEL_DECAY_CAP_SLOW = 3;
const WHEEL_DECAY_CAP_FAST = 6;
const WHEEL_DECAY_IDLE_MS = 500;

export type WheelAccelState = {
  time: number;
  mult: number;
  dir: 0 | 1 | -1;
  xtermJs: boolean;
  frac: number;
  base: number;
};

export function isXtermJsLike(): boolean {
  return process.env.TERM_PROGRAM === "vscode"
    || process.env.CURSOR_TRACE_ID !== undefined
    || process.env.WINDSURF_SESSION_ID !== undefined
    || process.env.VSCODE_GIT_IPC_HANDLE !== undefined;
}

export function readScrollSpeedBase(): number {
  const raw = process.env.NOCODE_SCROLL_SPEED || process.env.CLAUDE_CODE_SCROLL_SPEED;
  if (!raw) return 1;
  const value = Number.parseFloat(raw);
  if (Number.isNaN(value) || value <= 0) {
    return 1;
  }
  return Math.min(value, 20);
}

export function initWheelAccelState(): WheelAccelState {
  const base = readScrollSpeedBase();
  return {
    time: 0,
    mult: base,
    dir: 0,
    xtermJs: isXtermJsLike(),
    frac: 0,
    base,
  };
}

export function computeWheelStep(state: WheelAccelState, dir: 1 | -1, now: number): number {
  const gap = now - state.time;
  if (!state.xtermJs) {
    if (dir !== state.dir || gap > WHEEL_ACCEL_WINDOW_MS) {
      state.mult = state.base;
    } else {
      const cap = Math.max(WHEEL_ACCEL_MAX, state.base * 2);
      state.mult = Math.min(cap, state.mult + WHEEL_ACCEL_STEP);
    }
    state.dir = dir;
    state.time = now;
    return Math.max(1, Math.floor(state.mult));
  }

  const sameDir = dir === state.dir;
  state.time = now;
  state.dir = dir;
  if (sameDir && gap < WHEEL_BURST_MS) {
    return 1;
  }
  if (!sameDir || gap > WHEEL_DECAY_IDLE_MS) {
    state.mult = Math.max(2, state.base);
    state.frac = 0;
  } else {
    const m = Math.pow(0.5, gap / WHEEL_DECAY_HALFLIFE_MS);
    const cap = gap >= WHEEL_DECAY_GAP_MS ? WHEEL_DECAY_CAP_SLOW : WHEEL_DECAY_CAP_FAST;
    state.mult = Math.min(cap, 1 + (state.mult - 1) * m + WHEEL_DECAY_STEP * m);
  }
  const total = state.mult + state.frac;
  const rows = Math.max(1, Math.floor(total));
  state.frac = total - rows;
  return rows;
}

export function readCopyOnSelect(): boolean {
  const raw = process.env.NOCODE_COPY_ON_SELECT?.trim().toLowerCase();
  if (raw === "0" || raw === "false" || raw === "off") {
    return false;
  }
  if (raw === "1" || raw === "true" || raw === "on") {
    return true;
  }
  return true;
}

export function copyTextToNativeClipboard(text: string): void {
  if (process.env.SSH_CONNECTION) {
    return;
  }
  if (process.platform === "darwin") {
    const child = execFile("pbcopy", (error) => {
      void error;
    });
    child.stdin?.end(text);
    return;
  }
  if (process.platform === "win32") {
    const child = execFile("clip", (error) => {
      void error;
    });
    child.stdin?.end(text);
  }
}
