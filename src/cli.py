"""OpenCode-style CLI entrypoint built on the existing Python agent runtime."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
from pathlib import Path
import select
import sys
import termios
import threading
import tty
from typing import Any

import yaml
from rich import box
from rich.console import Console, Group
from rich.layout import Layout
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.prompt import Prompt
from rich.table import Table
from rich.text import Text

console = Console()

ACCENT = "#5FD7AF"
SECONDARY = "#8A99A6"
WARNING = "#F4D35E"
DANGER = "#FF6B6B"
USER_COLOR = "#7ED957"
SURFACE = "#11161C"
SURFACE_ALT = "#1A222B"


def _shorten_path(path: Path) -> str:
    try:
        return str(path.relative_to(Path.home()))
    except ValueError:
        return str(path)


def _is_stdin_piped() -> bool:
    return not sys.stdin.isatty()


def _read_stdin() -> str:
    if not _is_stdin_piped():
        return ""
    return sys.stdin.read().strip()


def _merge_config(config: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    merged = dict(config)
    if getattr(args, "model", None):
        merged["model"] = args.model
    if getattr(args, "subagent_model", None):
        merged["subagent_model"] = args.subagent_model
    if getattr(args, "base_url", None):
        merged["base_url"] = args.base_url
    if getattr(args, "max_tokens", None):
        merged["max_tokens"] = args.max_tokens
    if getattr(args, "temperature", None) is not None:
        merged["temperature"] = args.temperature
    return merged


def _render_status(agent) -> Table:
    cwd = _shorten_path(Path.cwd())
    table = Table(box=box.SIMPLE_HEAD, expand=True, show_header=False, padding=(0, 1))
    table.add_column(style=SECONDARY, width=10)
    table.add_column()
    table.add_column(style=SECONDARY, width=10)
    table.add_column()
    table.add_row("cwd", cwd, "model", agent.model_name or "-")
    table.add_row("subagent", agent.subagent_model_name or "-", "thread", agent.thread_id)
    return table


def _render_help() -> Panel:
    help_table = Table.grid(padding=(0, 2))
    help_table.add_column(style=ACCENT, justify="right")
    help_table.add_column()
    help_table.add_row("/help", "show help")
    help_table.add_row("/clear", "reset in-memory session")
    help_table.add_row("/session", "show current session state")
    help_table.add_row("/quit", "exit")
    help_table.add_row("ESC", "clear input / interrupt generation")
    return Panel(help_table, title="Commands", border_style=ACCENT, box=box.ROUNDED)


# ---------------------------------------------------------------------------
# ESC monitor – detects bare ESC or Ctrl+C during streaming
# ---------------------------------------------------------------------------

class _EscMonitor:
    """Background thread that watches stdin for interrupt keypresses.

    Sets terminal to raw-ish mode (no echo, no line-buffer, no ISIG)
    so that ESC and Ctrl+C are read as ordinary characters.
    The main async loop polls ``monitor.pressed``.
    """

    def __init__(self) -> None:
        self._pressed = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._old_term: list | None = None

    @property
    def pressed(self) -> bool:
        return self._pressed.is_set()

    def start(self) -> None:
        if not sys.stdin.isatty():
            return
        self._pressed.clear()
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=0.5)
            self._thread = None
        self._restore()

    def _run(self) -> None:
        fd = sys.stdin.fileno()
        try:
            self._old_term = termios.tcgetattr(fd)
            mode = termios.tcgetattr(fd)
            mode[tty.LFLAG] &= ~(termios.ECHO | termios.ICANON | termios.ISIG)
            mode[tty.CC][termios.VMIN] = 1
            mode[tty.CC][termios.VTIME] = 0
            termios.tcsetattr(fd, termios.TCSAFLUSH, mode)

            while not self._stop.is_set():
                ready, _, _ = select.select([sys.stdin], [], [], 0.1)
                if not ready:
                    continue
                ch = sys.stdin.read(1)
                if ch == "\x1b":
                    # bare ESC → interrupt; escape-sequence → drain
                    if select.select([sys.stdin], [], [], 0.02)[0]:
                        sys.stdin.read(1)
                        if select.select([sys.stdin], [], [], 0.02)[0]:
                            sys.stdin.read(1)
                        continue
                    self._pressed.set()
                    return
                if ch == "\x03":  # Ctrl+C → interrupt
                    self._pressed.set()
                    return
        except (OSError, ValueError):
            pass

    def _restore(self) -> None:
        if self._old_term:
            try:
                termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, self._old_term)
            except (OSError, ValueError):
                pass
            self._old_term = None


# ---------------------------------------------------------------------------
# Custom input – ESC clears buffer, Ctrl+C exits
# ---------------------------------------------------------------------------

_PROMPT_ANSI = "\x1b[1m\x1b[38;2;126;217;87myou\x1b[0m "


def _draw_prompt(buf: str, pos: int) -> None:
    sys.stdout.write(f"\r\x1b[K{_PROMPT_ANSI}{buf}")
    if pos < len(buf):
        sys.stdout.write(f"\x1b[{len(buf) - pos}D")
    sys.stdout.flush()


def _read_user_input() -> str | None:
    """Read user input with ESC-to-clear.

    Returns None on Ctrl+C / Ctrl+D (caller should exit).
    Falls back to Prompt.ask when stdin is not a TTY.
    """
    if not sys.stdin.isatty():
        try:
            return Prompt.ask(f"\n[bold {USER_COLOR}]you[/bold {USER_COLOR}]")
        except (EOFError, KeyboardInterrupt):
            return None

    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    buf = ""
    pos = 0

    sys.stdout.write(f"\r\x1b[K{_PROMPT_ANSI}")
    sys.stdout.flush()

    try:
        tty.setraw(fd)
        while True:
            ch = sys.stdin.read(1)

            if ch == "\x1b":
                if select.select([sys.stdin], [], [], 0.02)[0]:
                    seq = sys.stdin.read(1)
                    if seq == "[" and select.select([sys.stdin], [], [], 0.02)[0]:
                        arrow = sys.stdin.read(1)
                        if arrow == "D" and pos > 0:
                            pos -= 1
                        elif arrow == "C" and pos < len(buf):
                            pos += 1
                        elif arrow == "H":
                            pos = 0
                        elif arrow == "F":
                            pos = len(buf)
                    _draw_prompt(buf, pos)
                    continue
                buf = ""
                pos = 0
                _draw_prompt(buf, pos)
                continue

            if ch in ("\r", "\n"):
                sys.stdout.write("\r\n")
                sys.stdout.flush()
                return buf

            if ch in ("\x03", "\x04"):
                sys.stdout.write("\r\n")
                sys.stdout.flush()
                return None

            if ch in ("\x7f", "\x08"):
                if pos > 0:
                    buf = buf[: pos - 1] + buf[pos:]
                    pos -= 1
                    _draw_prompt(buf, pos)
                continue

            if ch == "\x15":
                buf = ""
                pos = 0
                _draw_prompt(buf, pos)
                continue

            if ord(ch[0]) >= 32:
                buf = buf[:pos] + ch + buf[pos:]
                pos += 1
                _draw_prompt(buf, pos)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def _render_topbar(agent) -> Panel:
    grid = Table.grid(expand=True)
    grid.add_column(ratio=3)
    grid.add_column(ratio=2, justify="center")
    grid.add_column(ratio=2, justify="right")
    grid.add_row(
        Text(" codeagent ", style=f"bold black on {ACCENT}"),
        Text(_shorten_path(Path.cwd()), style=f"bold {SECONDARY}"),
        Text(f"{agent.model_name or '-'}  [{agent.thread_id.split('-')[-1][:8]}]", style=SECONDARY),
    )
    return Panel(grid, border_style=ACCENT, box=box.SQUARE, padding=(0, 1), style=f"on {SURFACE}")


def _render_message(role: str, content: str) -> Panel:
    title = "assistant"
    border = ACCENT
    body: object = Markdown(content.strip() or " ")

    if role == "user":
        title = "you"
        border = USER_COLOR
        body = Text(content.strip() or " ", style="white")
    elif role == "system":
        title = "system"
        border = SECONDARY
        body = Text(content.strip() or " ", style=SECONDARY)

    return Panel(body, title=title, border_style=border, box=box.ROUNDED, padding=(0, 1))


def _format_tool_args(args: dict[str, Any], max_len: int = 60) -> str:
    if not args:
        return ""
    parts: list[str] = []
    for key, value in args.items():
        rendered = str(value)
        if len(rendered) > max_len:
            rendered = rendered[:max_len] + "..."
        parts.append(f"{key}={rendered}")
    return ", ".join(parts)


def _build_live_renderable(content: str, active_tools: list[dict], finished_tools: list[dict]):
    parts: list[object] = []
    body = content.strip()

    if body:
        parts.append(Markdown(body))
    else:
        parts.append(Text("thinking...", style=SECONDARY))

    if active_tools or finished_tools:
        tool_lines: list[object] = []
        for tool in active_tools[-4:]:
            args_text = _format_tool_args(tool.get("args", {}))
            line = f"  ● {tool['name']}"
            if args_text:
                line += f"({args_text})"
            tool_lines.append(Text(line, style=WARNING))
        for tool in finished_tools[-4:]:
            args_text = _format_tool_args(tool.get("args", {}))
            line = f"  ✓ {tool['name']}"
            if args_text:
                line += f"({args_text})"
            tool_lines.append(Text(line, style=ACCENT))
        parts.append(Text(""))
        parts.extend(tool_lines)

    return Group(*parts)


def _render_activity(active_tools: list[dict], finished_tools: list[dict]) -> Panel:
    lines: list[Text] = []
    for tool in active_tools[-4:]:
        args_text = _format_tool_args(tool.get("args", {}))
        line = f"● {tool['name']}"
        if args_text:
            line += f"({args_text})"
        lines.append(Text(line, style=WARNING))
    for tool in finished_tools[-6:]:
        args_text = _format_tool_args(tool.get("args", {}))
        line = f"✓ {tool['name']}"
        if args_text:
            line += f"({args_text})"
        lines.append(Text(line, style=ACCENT))
    if not lines:
        lines.append(Text("No tool activity yet.", style=SECONDARY))
    return Panel(Group(*lines), title="Activity", border_style=SECONDARY, box=box.ROUNDED, padding=(0, 1))


def _render_composer(last_input: str = "") -> Panel:
    body = Text(last_input or "Type a prompt below. Commands: /help /clear /session /quit", style="white" if last_input else SECONDARY)
    return Panel(body, title="Composer", border_style=ACCENT, box=box.ROUNDED, padding=(0, 1))


def _render_footer() -> Panel:
    footer = Table.grid(expand=True)
    footer.add_column(ratio=1)
    footer.add_column(ratio=1, justify="center")
    footer.add_column(ratio=1, justify="right")
    footer.add_row(
        Text("Enter to submit", style=SECONDARY),
        Text("OpenCode-style terminal layout", style=SECONDARY),
        Text("/help for commands", style=SECONDARY),
    )
    return Panel(footer, border_style=SECONDARY, box=box.SQUARE, padding=(0, 1), style=f"on {SURFACE_ALT}")


def _render_transcript(history: list[dict[str, str]], streaming: str = "", active_tools: list[dict] | None = None, finished_tools: list[dict] | None = None):
    panels: list[object] = []
    for message in history[-10:]:
        panels.append(_render_message(message["role"], message["content"]))

    if streaming or active_tools or finished_tools:
        assistant_body = _build_live_renderable(streaming, active_tools or [], finished_tools or [])
        panels.append(
            Panel(
                assistant_body,
                title="assistant",
                border_style=ACCENT,
                box=box.ROUNDED,
                padding=(0, 1),
            )
        )

    if not panels:
        panels.append(
            Panel(
                Text("No messages yet. Start with a task or question.", style=SECONDARY),
                title="assistant",
                border_style=ACCENT,
                box=box.ROUNDED,
                padding=(0, 1),
            )
        )

    return Panel(Group(*panels), title="Session", border_style=ACCENT, box=box.ROUNDED, padding=(0, 1))


def _render_chat_layout(
    agent,
    history: list[dict[str, str]],
    last_input: str = "",
    streaming: str = "",
    active_tools: list[dict] | None = None,
    finished_tools: list[dict] | None = None,
) -> Layout:
    layout = Layout()
    layout.split_column(
        Layout(_render_topbar(agent), size=3, name="top"),
        Layout(name="body"),
        Layout(_render_footer(), size=3, name="bottom"),
    )
    layout["body"].split_row(
        Layout(_render_transcript(history, streaming, active_tools, finished_tools), ratio=4, name="transcript"),
        Layout(name="sidebar", ratio=2),
    )
    layout["sidebar"].split_column(
        Layout(Panel(_render_status(agent), title="Context", border_style=SECONDARY, box=box.ROUNDED, padding=(0, 1)), ratio=2),
        Layout(_render_activity(active_tools or [], finished_tools or []), ratio=2),
        Layout(_render_composer(last_input), ratio=1),
    )
    return layout


def load_config(config_path: str | None = None) -> dict[str, Any]:
    resolved = config_path or os.environ.get("BF_CONFIG", "config/default.yaml")
    try:
        with open(resolved, encoding="utf-8") as handle:
            return yaml.safe_load(handle) or {}
    except FileNotFoundError:
        console.print(f"[yellow]config file {resolved} not found, using defaults.[/yellow]")
        return {}


def build_agent(config: dict[str, Any]):
    from src.agent import create_codeagent

    api_key = os.environ.get("ZHIPU_API_KEY", config.get("api_key", ""))
    if not api_key:
        console.print("[red]missing API key: set ZHIPU_API_KEY first.[/red]")
        sys.exit(1)

    return create_codeagent(
        api_key=api_key,
        model=config.get("model", "glm-4-flash"),
        base_url=config.get("base_url", "https://open.bigmodel.cn/api/paas/v4"),
        max_tokens=config.get("max_tokens", 4096),
        temperature=config.get("temperature", 0.7),
        compression=config.get("compression"),
        subagent_model=config.get("subagent_model"),
        subagent_temperature=config.get("subagent_temperature", 0.1),
    )


async def _stream_prompt(agent, prompt: str, plain: bool = False) -> tuple[str, bool]:
    """Stream agent response.  Returns (buffer, interrupted)."""
    buffer = ""
    active_tools: list[dict] = []
    finished_tools: list[dict] = []
    interrupted = False

    monitor = _EscMonitor()
    monitor.start()

    try:
        if plain:
            async for event_type, *data in agent.chat(prompt):
                if monitor.pressed:
                    interrupted = True
                    break
                if event_type == "text":
                    chunk = data[0]
                    buffer += chunk
                    console.print(chunk, end="")
            if buffer and not buffer.endswith("\n"):
                console.print()
            return buffer, interrupted

        with Live(
            _render_chat_layout(agent, [{"role": "user", "content": prompt}], last_input=prompt, streaming="", active_tools=active_tools, finished_tools=finished_tools),
            console=console,
            refresh_per_second=8,
            transient=False,
        ) as live:
            async for event_type, *data in agent.chat(prompt):
                if monitor.pressed:
                    interrupted = True
                    break
                if event_type == "text":
                    buffer += data[0]
                    live.update(
                        _render_chat_layout(
                            agent,
                            [{"role": "user", "content": prompt}],
                            last_input=prompt,
                            streaming=buffer,
                            active_tools=active_tools,
                            finished_tools=finished_tools,
                        ),
                        refresh=True,
                    )
                elif event_type == "tool_start":
                    tool_name = data[0]
                    tool_args = data[1] if len(data) > 1 else {}
                    active_tools.append({"name": tool_name, "args": tool_args})
                    live.update(
                        _render_chat_layout(
                            agent,
                            [{"role": "user", "content": prompt}],
                            last_input=prompt,
                            streaming=buffer,
                            active_tools=active_tools,
                            finished_tools=finished_tools,
                        ),
                        refresh=True,
                    )
                elif event_type == "tool_end":
                    tool_name = data[0]
                    matched = next(
                        (tool for tool in active_tools if tool["name"] == tool_name),
                        {"name": tool_name, "args": {}},
                    )
                    if matched in active_tools:
                        active_tools.remove(matched)
                    finished_tools.append(matched)
                    live.update(
                        _render_chat_layout(
                            agent,
                            [{"role": "user", "content": prompt}],
                            last_input=prompt,
                            streaming=buffer,
                            active_tools=active_tools,
                            finished_tools=finished_tools,
                        ),
                        refresh=True,
                    )
    finally:
        monitor.stop()

    return buffer, interrupted


async def run_chat(agent) -> None:
    history: list[dict[str, str]] = []

    while True:
        console.clear()
        console.print(_render_chat_layout(agent, history))
        user_input = _read_user_input()
        if user_input is None:
            console.clear()
            break

        user_input = user_input.strip()
        if not user_input:
            continue

        if user_input.startswith("/"):
            cmd = user_input.lower()
            if cmd in ("/quit", "/exit"):
                console.clear()
                break
            if cmd == "/clear":
                agent.clear()
                history.clear()
                continue
            if cmd == "/help":
                console.clear()
                console.print(_render_chat_layout(agent, history, last_input=user_input))
                console.print()
                console.print(_render_help())
                Prompt.ask(f"\n[bold {SECONDARY}]continue[/bold {SECONDARY}]", default="")
                continue
            if cmd == "/session":
                console.clear()
                console.print(_render_chat_layout(agent, history, last_input=user_input))
                console.print()
                console.print(Panel(_render_status(agent), title="Context", border_style=SECONDARY, box=box.ROUNDED))
                Prompt.ask(f"\n[bold {SECONDARY}]continue[/bold {SECONDARY}]", default="")
                continue
            console.clear()
            console.print(_render_chat_layout(agent, history, last_input=user_input))
            console.print(f"\n[{WARNING}]unknown command:[/] {user_input}")
            Prompt.ask(f"\n[bold {SECONDARY}]continue[/bold {SECONDARY}]", default="")
            continue

        try:
            history.append({"role": "user", "content": user_input})
            response, interrupted = await _stream_prompt(agent, user_input, plain=False)
            if interrupted:
                console.print(f"\n[{WARNING}]Interrupted.[/]")
            if response.strip():
                history.append({"role": "assistant", "content": response})
        except Exception as error:
            history.append({"role": "system", "content": f"error: {error}"})
            logging.exception("chat error")


def _render_models(config: dict[str, Any]) -> None:
    table = Table(title="Configured Models", box=box.ROUNDED, border_style=ACCENT)
    table.add_column("Field", style=SECONDARY)
    table.add_column("Value", style="white")
    table.add_row("model", str(config.get("model", "glm-4-flash")))
    table.add_row("subagent_model", str(config.get("subagent_model", config.get("model", "glm-4-flash"))))
    table.add_row("base_url", str(config.get("base_url", "https://open.bigmodel.cn/api/paas/v4")))
    table.add_row("max_tokens", str(config.get("max_tokens", 4096)))
    table.add_row("temperature", str(config.get("temperature", 0.7)))
    console.print(table)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="codeagent",
        description="OpenCode-style CLI shell backed by the existing Python agent runtime.",
    )
    parser.add_argument("--config", help="Path to YAML config file.")
    parser.add_argument("--model", help="Override the primary model.")
    parser.add_argument("--subagent-model", dest="subagent_model", help="Override the subagent model.")
    parser.add_argument("--base-url", dest="base_url", help="Override the model API base URL.")
    parser.add_argument("--max-tokens", dest="max_tokens", type=int, help="Override max tokens.")
    parser.add_argument("--temperature", type=float, help="Override temperature.")
    subparsers = parser.add_subparsers(dest="command")

    run_parser = subparsers.add_parser("run", help="Run a single prompt and exit.")
    run_parser.add_argument("prompt", nargs="*", help="Prompt text.")
    run_parser.add_argument("--plain", action="store_true", help="Print plain streamed text without panels.")

    subparsers.add_parser("models", help="Show resolved model configuration.")
    subparsers.add_parser("chat", help="Start the interactive shell explicitly.")
    return parser


def _resolve_command(args: argparse.Namespace, root_prompt: str) -> tuple[str, str]:
    stdin_prompt = _read_stdin()

    if args.command == "run":
        prompt = " ".join(args.prompt).strip() or stdin_prompt
        return "run", prompt

    if args.command == "models":
        return "models", ""

    if args.command == "chat":
        return "chat", ""

    if root_prompt:
        return "run", root_prompt
    if stdin_prompt:
        return "run", stdin_prompt
    return "chat", ""


async def _main_async(args: argparse.Namespace) -> int:
    config = _merge_config(load_config(args.config), args)
    command, prompt = _resolve_command(args, getattr(args, "_root_prompt", ""))

    if command == "models":
        _render_models(config)
        return 0

    agent = build_agent(config)

    if command == "chat":
        await run_chat(agent)
        return 0

    if not prompt:
        console.print(f"[{DANGER}]no prompt provided.[/]")
        return 1

    try:
        _, interrupted = await _stream_prompt(agent, prompt, plain=getattr(args, "plain", False))
        if interrupted:
            console.print(f"\n[{WARNING}]Interrupted.[/]")
        return 0
    except Exception as error:
        console.print(f"[{DANGER}]run failed:[/] {error}")
        logging.exception("run error")
        return 1


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    parser = _build_parser()
    args, extras = parser.parse_known_args()
    root_prompt = " ".join(extras).strip() if args.command is None else ""
    setattr(args, "_root_prompt", root_prompt)
    raise SystemExit(asyncio.run(_main_async(args)))


if __name__ == "__main__":
    main()
