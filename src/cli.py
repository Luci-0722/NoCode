"""CLI 入口：更接近 Claude Code 风格的 Rich 终端交互。"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
import sys

import yaml
from rich import box
from rich.console import Console, Group
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.prompt import Prompt
from rich.table import Table
from rich.text import Text

from src.agent import create_codeagent

console = Console()

ACCENT = "#5FD7AF"
SECONDARY = "#8A99A6"
WARNING = "#F4D35E"
DANGER = "#FF6B6B"
USER_COLOR = "#7ED957"


def _shorten_path(path: Path) -> str:
    try:
        return str(path.relative_to(Path.home()))
    except ValueError:
        return str(path)


def _render_banner(agent) -> Panel:
    grid = Table.grid(expand=True)
    grid.add_column(ratio=2)
    grid.add_column(justify="right")
    grid.add_row(
        Text("codeagent", style=f"bold {ACCENT}"),
        Text("interactive coding shell", style=SECONDARY),
    )
    grid.add_row(
        Text("参考 Claude Code / OpenCode / OpenClaw 的终端节奏", style="white"),
        Text(f"session {agent.thread_id.split('-')[-1][:8]}", style=SECONDARY),
    )
    return Panel(
        grid,
        border_style=ACCENT,
        box=box.ROUNDED,
        padding=(0, 1),
    )


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
    help_table.add_row("/help", "显示帮助")
    help_table.add_row("/clear", "重置会话线程")
    help_table.add_row("/session", "查看当前会话状态")
    help_table.add_row("/quit", "退出程序")
    return Panel(help_table, title="命令", border_style=ACCENT, box=box.ROUNDED)


def _render_user_message(content: str) -> Panel:
    return Panel(
        Text(content, style="white"),
        title=f"[bold {USER_COLOR}]你[/bold {USER_COLOR}]",
        border_style=USER_COLOR,
        box=box.ROUNDED,
        padding=(0, 1),
    )


def _build_live_renderable(content: str, active_tools: list[str], finished_tools: list[str]):
    """构建 Live 内部的轻量渲染（不用 Panel，避免重复叠加）。"""
    parts: list[object] = []

    body = content.strip()
    if body:
        parts.append(Markdown(body))
    else:
        parts.append(Text("正在思考...", style=SECONDARY))

    if active_tools or finished_tools:
        tool_lines: list[object] = []
        for tool_name in active_tools[-4:]:
            tool_lines.append(Text(f"  ● 运行中: {tool_name}", style=WARNING))
        for tool_name in finished_tools[-4:]:
            tool_lines.append(Text(f"  ✓ 已完成: {tool_name}", style=ACCENT))
        parts.append(Text(""))  # 空行分隔
        parts.extend(tool_lines)

    return Group(*parts)


def _render_assistant_final(content: str, finished_tools: list[str]):
    """流式结束后，打印最终带 Panel 的结果。"""
    body = content.strip()
    if not body:
        return

    console.print(Panel(
        Markdown(body),
        title=f"[bold {ACCENT}]助手[/bold {ACCENT}]",
        border_style=ACCENT,
        box=box.ROUNDED,
        padding=(0, 1),
    ))

    if finished_tools:
        tool_table = Table.grid(expand=True)
        tool_table.add_column()
        for tool_name in finished_tools[-8:]:
            tool_table.add_row(f"[{ACCENT}]✓[/{ACCENT}] [bold]{tool_name}[/bold]")
        console.print(Panel(
            tool_table,
            title="工具活动",
            border_style=SECONDARY,
            box=box.ROUNDED,
            padding=(0, 1),
        ))


def load_config() -> dict:
    config_path = os.environ.get("BF_CONFIG", "config/default.yaml")
    try:
        with open(config_path) as f:
            return yaml.safe_load(f) or {}
    except FileNotFoundError:
        console.print(f"[yellow]配置文件 {config_path} 未找到，使用默认配置。[/yellow]")
        return {}


def build_agent(config: dict):
    api_key = os.environ.get("ZHIPU_API_KEY", config.get("api_key", ""))
    if not api_key:
        console.print("[red]错误：未设置 API Key。请设置 ZHIPU_API_KEY 环境变量。[/red]")
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


async def run_chat(agent):
    console.print(_render_banner(agent))
    console.print(_render_status(agent))
    console.print(f"[{SECONDARY}]命令: /help /clear /session /quit[/]\n")

    while True:
        try:
            user_input = Prompt.ask(f"\n[bold {USER_COLOR}]you[/bold {USER_COLOR}]")
        except (EOFError, KeyboardInterrupt):
            console.print("\n[yellow]再见！[/yellow]")
            break

        user_input = user_input.strip()
        if not user_input:
            continue

        # 处理命令
        if user_input.startswith("/"):
            cmd = user_input.lower()
            if cmd in ("/quit", "/exit"):
                console.print("[yellow]再见！[/yellow]")
                break
            if cmd == "/clear":
                agent.clear()
                console.print(Panel("已创建新会话线程。", border_style=ACCENT, box=box.ROUNDED))
                console.print(_render_status(agent))
                continue
            if cmd == "/help":
                console.print(_render_help())
                continue
            if cmd == "/session":
                console.print(_render_status(agent))
                continue
            console.print(f"[{WARNING}]未知命令:[/] {user_input}")
            continue

        # 流式对话
        try:
            console.print()
            console.print(_render_user_message(user_input))

            buffer = ""
            active_tools: list[str] = []
            finished_tools: list[str] = []

            # 流式阶段：用 transient Live 显示纯文本，避免 Panel 叠加
            with Live(
                Text("正在思考...", style=SECONDARY),
                console=console,
                refresh_per_second=8,
                transient=True,
            ) as live:
                async for event_type, *data in agent.chat(user_input):
                    if event_type == "text":
                        buffer += data[0]
                        live.update(
                            _build_live_renderable(buffer, active_tools, finished_tools),
                            refresh=True,
                        )
                    elif event_type == "tool_start":
                        active_tools.append(data[0])
                        live.update(
                            _build_live_renderable(buffer, active_tools, finished_tools),
                            refresh=True,
                        )
                    elif event_type == "tool_end":
                        tool_name = data[0]
                        if tool_name in active_tools:
                            active_tools.remove(tool_name)
                        finished_tools.append(tool_name)
                        live.update(
                            _build_live_renderable(buffer, active_tools, finished_tools),
                            refresh=True,
                        )

            # 结束后：打印最终带 Panel 的结果（只打印一次）
            _render_assistant_final(buffer, finished_tools)
            console.print()
        except Exception as e:
            console.print(f"\n[{DANGER}]错误:[/] {e}")
            logging.exception("Chat error")


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    config = load_config()
    agent = build_agent(config)
    try:
        asyncio.run(run_chat(agent))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
