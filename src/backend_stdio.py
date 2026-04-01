"""Persistent stdio backend for the TypeScript TUI frontend."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import Any

import yaml

from src.agent import create_mainagent


def _load_config() -> dict[str, Any]:
    config_path = os.environ.get("BF_CONFIG", "config/default.yaml")
    try:
        with open(config_path, encoding="utf-8") as handle:
            return yaml.safe_load(handle) or {}
    except FileNotFoundError:
        return {}


def _build_agent(config: dict[str, Any]):
    api_key = os.environ.get("ZHIPU_API_KEY", config.get("api_key", ""))
    if not api_key:
        raise RuntimeError("missing API key: set ZHIPU_API_KEY first")

    return create_mainagent(
        api_key=api_key,
        model=config.get("model", "glm-4-flash"),
        base_url=config.get("base_url", "https://open.bigmodel.cn/api/paas/v4"),
        max_tokens=config.get("max_tokens", 4096),
        temperature=config.get("temperature", 0.7),
        compression=config.get("compression"),
        subagent_model=config.get("subagent_model"),
        subagent_temperature=config.get("subagent_temperature", 0.1),
    )


def _emit(event: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(event, ensure_ascii=False) + "\n")
    sys.stdout.flush()


async def _stream_prompt(agent, prompt: str) -> None:
    async for event_type, *data in agent.chat(prompt):
        if event_type == "text":
            _emit({"type": "text", "delta": data[0]})
        elif event_type == "tool_start":
            _emit(
                {
                    "type": "tool_start",
                    "name": data[0],
                    "args": data[1] if len(data) > 1 else {},
                }
            )
        elif event_type == "tool_end":
            _emit({"type": "tool_end", "name": data[0]})
    _emit({"type": "done"})


async def _handle_message(agent, payload: dict[str, Any]) -> bool:
    message_type = payload.get("type")

    if message_type == "prompt":
        prompt = str(payload.get("text", "")).strip()
        if not prompt:
            _emit({"type": "error", "message": "empty prompt"})
            return True
        await _stream_prompt(agent, prompt)
        return True

    if message_type == "clear":
        agent.clear()
        _emit({"type": "cleared", "thread_id": agent.thread_id})
        return True

    if message_type == "status":
        _emit(
            {
                "type": "status",
                "thread_id": agent.thread_id,
                "model": agent.model_name,
                "subagent_model": agent.subagent_model_name,
                "cwd": os.getcwd(),
            }
        )
        return True

    if message_type == "exit":
        return False

    _emit({"type": "error", "message": f"unknown message type: {message_type}"})
    return True


async def main() -> int:
    try:
        config = _load_config()
        agent = _build_agent(config)
    except Exception as error:
        _emit({"type": "fatal", "message": str(error)})
        return 1

    _emit(
        {
            "type": "hello",
            "thread_id": agent.thread_id,
            "model": agent.model_name,
            "subagent_model": agent.subagent_model_name,
            "cwd": os.getcwd(),
        }
    )

    while True:
        line = await asyncio.to_thread(sys.stdin.readline)
        if not line:
            break
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as error:
            _emit({"type": "error", "message": f"invalid json: {error}"})
            continue

        should_continue = await _handle_message(agent, payload)
        if not should_continue:
            break

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
