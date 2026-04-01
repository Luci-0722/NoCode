"""CLI 入口：Rich REPL + 流式输出。"""

from __future__ import annotations

import asyncio
import logging
import os
import sys

import yaml
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt

from src.agent import create_codeagent

console = Console()

BANNER = """
[bold cyan]
╔══════════════════════════════════╗
║          codeagent             ║
║       你的中文代码代理          ║
╚══════════════════════════════════╝
[/bold cyan]
"""


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
    console.print(BANNER)
    console.print("[dim]命令: /quit 退出 | /clear 清除对话 | /help 帮助[/dim]\n")

    while True:
        try:
            user_input = Prompt.ask("\n[bold green]你[/bold green]")
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
            elif cmd == "/clear":
                agent.clear()
                console.print("[cyan]对话已清除，新会话已开始。[/cyan]")
                continue
            elif cmd == "/help":
                console.print(Panel(
                    "  /quit  - 退出程序\n  /clear - 清除对话，开始新会话\n  /help  - 显示帮助",
                    title="命令列表",
                    border_style="cyan",
                ))
                continue
            else:
                console.print(f"[yellow]未知命令: {user_input}[/yellow]")
                continue

        # 流式对话
        try:
            console.print()
            async for event_type, *data in agent.chat(user_input):
                if event_type == "text":
                    console.print(data[0], end="")
                elif event_type == "tool_start":
                    console.print(f"\n[dim]  ⚙ {data[0]}[/dim]", end="")
                elif event_type == "tool_end":
                    console.print(" [dim]✓[/dim]", end="")
            console.print("\n")
        except Exception as e:
            console.print(f"\n[red]错误: {e}[/red]")
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
