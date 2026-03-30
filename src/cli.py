"""CLI 入口：Rich REPL + v2 流式输出。"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import uuid

import yaml
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt

from src.agent import create_bf_agent

console = Console()

BANNER = """
[bold cyan]
╔══════════════════════════════════╗
║        Best Friend AI           ║
║       你的 AI 伙伴 · 小智       ║
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

    return create_bf_agent(
        api_key=api_key,
        model=config.get("model", "glm-4-flash"),
        base_url=config.get("base_url", "https://open.bigmodel.cn/api/paas/v4"),
        max_tokens=config.get("max_tokens", 4096),
        temperature=config.get("temperature", 0.7),
        compression=config.get("compression"),
    )


async def run_chat(agent):
    console.print(BANNER)
    console.print("[dim]命令: /quit 退出 | /clear 清除对话 | /help 帮助[/dim]\n")

    thread_id = str(uuid.uuid4())[:8]
    config = {"configurable": {"thread_id": thread_id}}

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
                thread_id = str(uuid.uuid4())[:8]
                config = {"configurable": {"thread_id": thread_id}}
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

        # 流式调用 agent（v2 格式）
        try:
            console.print()
            async for chunk in agent.astream(
                {"messages": [{"role": "user", "content": user_input}]},
                config=config,
                stream_mode="messages",
                version="v2",
            ):
                if chunk["type"] == "messages":
                    token, metadata = chunk["data"]
                    if metadata.get("langgraph_node") != "model":
                        continue
                    # 只显示 text 类型的内容块
                    if hasattr(token, "content_blocks"):
                        for block in token.content_blocks:
                            if block.get("type") == "text" and block.get("text"):
                                console.print(block["text"], end="")
                    elif hasattr(token, "content") and token.content:
                        console.print(token.content, end="")

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
