from __future__ import annotations

import argparse
import asyncio
import os
import sys
from typing import Any

from rich.console import Console
from rich.prompt import Prompt
from rich.table import Table

from nocode_agent.agent import create_mainagent
from nocode_agent.config import load_config

console = Console()


def merge_config(config: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    merged = dict(config)
    for key in ("model", "subagent_model", "base_url", "max_tokens"):
        value = getattr(args, key, None)
        if value:
            merged[key] = value
    if getattr(args, "temperature", None) is not None:
        merged["temperature"] = args.temperature
    return merged


async def build_agent(config: dict[str, Any]):
    api_key = os.environ.get("ZHIPU_API_KEY", config.get("api_key", ""))
    if not api_key:
        console.print("[red]missing API key: set ZHIPU_API_KEY first.[/red]")
        raise SystemExit(1)

    return await create_mainagent(
        api_key=api_key,
        model=config.get("model", "glm-4-flash"),
        base_url=config.get("base_url", "https://open.bigmodel.cn/api/paas/v4"),
        max_tokens=config.get("max_tokens", 4096),
        temperature=config.get("temperature", 0.7),
        compression=config.get("compression"),
        subagent_model=config.get("subagent_model"),
        subagent_temperature=config.get("subagent_temperature", 0.1),
        persistence_config=config,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="nocode", description="NoCode")
    parser.add_argument("--config", help="Path to YAML config file.")
    parser.add_argument("--model", help="Override the primary model.")
    parser.add_argument("--subagent-model", dest="subagent_model", help="Override the subagent model.")
    parser.add_argument("--base-url", dest="base_url", help="Override the model API base URL.")
    parser.add_argument("--max-tokens", dest="max_tokens", type=int, help="Override max tokens.")
    parser.add_argument("--temperature", type=float, help="Override temperature.")
    subparsers = parser.add_subparsers(dest="command")

    run_parser = subparsers.add_parser("run", help="Run a single prompt and exit.")
    run_parser.add_argument("prompt", nargs="*", help="Prompt text.")

    subparsers.add_parser("models", help="Show resolved model configuration.")
    subparsers.add_parser("chat", help="Start a simple interactive shell.")
    return parser


def read_stdin() -> str:
    if sys.stdin.isatty():
        return ""
    return sys.stdin.read().strip()


def resolve_command(args: argparse.Namespace, root_prompt: str) -> tuple[str, str]:
    stdin_prompt = read_stdin()

    if args.command == "run":
        return "run", " ".join(args.prompt).strip() or stdin_prompt
    if args.command == "models":
        return "models", ""
    if args.command == "chat":
        return "chat", ""
    if root_prompt:
        return "run", root_prompt
    if stdin_prompt:
        return "run", stdin_prompt
    return "chat", ""


async def stream_prompt(agent, prompt: str) -> str:
    chunks: list[str] = []
    async for event_type, *data in agent.chat(prompt):
        if event_type == "text":
            chunk = data[0]
            chunks.append(chunk)
            console.print(chunk, end="")
    if chunks and not chunks[-1].endswith("\n"):
        console.print()
    return "".join(chunks)


async def run_chat(agent) -> None:
    while True:
        try:
            user_input = Prompt.ask("[bold green]❯[/bold green]")
        except (EOFError, KeyboardInterrupt):
            console.print()
            break

        user_input = user_input.strip()
        if not user_input:
            continue
        if user_input in {"/quit", "/exit"}:
            break
        if user_input == "/clear":
            await agent.clear()
            console.clear()
            continue
        if user_input == "/help":
            console.print("[dim]Commands: /help /clear /quit[/dim]")
            continue
        console.print("[bold cyan]⏺[/bold cyan] ", end="")
        await stream_prompt(agent, user_input)


def render_models(config: dict[str, Any]) -> None:
    table = Table(title="Configured Models")
    table.add_column("Field")
    table.add_column("Value")
    table.add_row("model", str(config.get("model", "glm-4-flash")))
    table.add_row("subagent_model", str(config.get("subagent_model", config.get("model", "glm-4-flash"))))
    table.add_row("base_url", str(config.get("base_url", "https://open.bigmodel.cn/api/paas/v4")))
    table.add_row("max_tokens", str(config.get("max_tokens", 4096)))
    table.add_row("temperature", str(config.get("temperature", 0.7)))
    console.print(table)


async def main_async(args: argparse.Namespace) -> int:
    config = merge_config(load_config(args.config), args)
    command, prompt = resolve_command(args, getattr(args, "_root_prompt", ""))

    if command == "models":
        render_models(config)
        return 0

    agent = await build_agent(config)

    if command == "chat":
        await run_chat(agent)
        return 0

    if not prompt:
        console.print("[red]no prompt provided.[/red]")
        return 1

    await stream_prompt(agent, prompt)
    return 0


def main() -> None:
    parser = build_parser()
    args, extras = parser.parse_known_args()
    setattr(args, "_root_prompt", " ".join(extras).strip() if args.command is None else "")
    raise SystemExit(asyncio.run(main_async(args)))


if __name__ == "__main__":
    main()
