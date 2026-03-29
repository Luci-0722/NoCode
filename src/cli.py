"""CLI entry point for best_friend agent."""

from __future__ import annotations

import asyncio
import logging
import os
import sys

import yaml
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt
from rich.text import Text

from src.agent.loop import Agent
from src.core import AgentConfig

logger = logging.getLogger(__name__)
console = Console()

BANNER = """
[bold cyan]
╔══════════════════════════════════╗
║        🤖 Best Friend AI        ║
║       Your AI Companion         ║
╚══════════════════════════════════╝
[/bold cyan]
"""


def load_config() -> AgentConfig:
    config_path = os.environ.get("BF_CONFIG", "config/default.yaml")
    try:
        with open(config_path) as f:
            data = yaml.safe_load(f) or {}
    except FileNotFoundError:
        console.print(f"[yellow]Config not found at {config_path}, using defaults.[/yellow]")
        data = {}

    api_key = os.environ.get("ZHIPU_API_KEY", data.get("api_key", ""))
    if not api_key:
        console.print("[red]Error: No API key set. Set ZHIPU_API_KEY env var or api_key in config.[/red]")
        sys.exit(1)

    return AgentConfig(
        model=data.get("model", "glm-4-flash"),
        api_key=api_key,
        base_url=data.get("base_url", "https://open.bigmodel.cn/api/paas/v4"),
        max_tokens=data.get("max_tokens", 4096),
        temperature=data.get("temperature", 0.7),
        system_prompt=data.get("system_prompt", AgentConfig.system_prompt),
        max_short_term_messages=data.get("max_short_term_messages", 50),
        max_tool_rounds=data.get("max_tool_rounds", 10),
        skills_dir=data.get("skills_dir", "skills"),
        data_dir=data.get("data_dir", "data"),
    )


async def run_chat(agent: Agent) -> None:
    console.print(BANNER)
    console.print("[dim]Commands: /quit, /clear, /skills, /memory, /help[/dim]\n")

    while True:
        try:
            user_input = Prompt.ask("\n[bold green]You[/bold green]")
        except (EOFError, KeyboardInterrupt):
            console.print("\n[yellow]Goodbye! 👋[/yellow]")
            break

        user_input = user_input.strip()
        if not user_input:
            continue

        # Handle commands
        if user_input.startswith("/"):
            cmd = user_input.lower()
            if cmd == "/quit" or cmd == "/exit":
                console.print("[yellow]Goodbye! 👋[/yellow]")
                break
            elif cmd == "/clear":
                agent.short_term.clear()
                console.print("[cyan]Short-term memory cleared.[/cyan]")
                continue
            elif cmd == "/skills":
                skills = agent.skills.list_skills()
                console.print(Panel(
                    "\n".join(f"  • {s}" for s in skills),
                    title="Available Skills",
                    border_style="cyan",
                ))
                continue
            elif cmd == "/memory":
                facts = agent.long_term.get_facts(limit=20)
                if facts:
                    lines = [f"  [#{f['id']}] ({f['category']}) {f['content']}" for f in facts]
                    console.print(Panel("\n".join(lines), title="Long-term Memory", border_style="cyan"))
                else:
                    console.print("[dim]No facts stored yet.[/dim]")
                continue
            elif cmd == "/tasks":
                tasks = agent.scheduler.list_tasks()
                if tasks:
                    lines = []
                    for t in tasks:
                        status = "✅" if t["enabled"] else "⏸️"
                        lines.append(f"  {status} [{t['id']}] {t['name']} (next: {t['next_run']})")
                    console.print(Panel("\n".join(lines), title="Scheduled Tasks", border_style="cyan"))
                else:
                    console.print("[dim]No scheduled tasks.[/dim]")
                continue
            elif cmd == "/help":
                console.print(Panel(
                    "  /quit   - Exit\n"
                    "  /clear  - Clear short-term memory\n"
                    "  /skills - List available skills\n"
                    "  /memory - Show long-term memory\n"
                    "  /tasks  - List scheduled tasks\n"
                    "  /help   - Show this help",
                    title="Commands",
                    border_style="cyan",
                ))
                continue
            else:
                console.print(f"[yellow]Unknown command: {user_input}[/yellow]")
                continue

        # Chat with streaming
        try:
            console.print()
            full_response = ""
            async for chunk in agent.chat_stream(user_input):
                if isinstance(chunk, str):
                    console.print(chunk, end="")
                    full_response += chunk
            console.print("\n")
        except Exception as e:
            console.print(f"[red]Error: {e}[/red]")
            logger.exception("Chat error")


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    config = load_config()
    agent = Agent(config)

    try:
        asyncio.run(_run(agent))
    except KeyboardInterrupt:
        pass


async def _run(agent: Agent) -> None:
    await agent.start()
    try:
        await run_chat(agent)
    finally:
        await agent.stop()


if __name__ == "__main__":
    main()
