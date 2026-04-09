"""Persistent stdio backend for the TypeScript TUI frontend."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from typing import Any

from nocode_agent.agent import create_mainagent
from nocode_agent.config import load_config, resolve_api_key, resolve_no_proxy, resolve_proxy
from nocode_agent.log import setup_logging
from nocode_agent.persistence import list_threads, load_thread_messages, resolve_checkpoint_path

logger = logging.getLogger(__name__)


def _load_config() -> dict[str, Any]:
    return load_config()


async def _build_agent(config: dict[str, Any]):
    api_key = resolve_api_key(config)
    if not api_key:
        raise RuntimeError("missing API key: set NOCODE_API_KEY/OPENAI_API_KEY/OLLAMA_API_KEY/ZHIPU_API_KEY, or configure api_key")

    return await create_mainagent(
        api_key=api_key,
        model=config.get("model", "glm-4-flash"),
        base_url=config.get("base_url", "https://open.bigmodel.cn/api/paas/v4"),
        max_tokens=config.get("max_tokens", 4096),
        temperature=config.get("temperature", 0.7),
        compression=config.get("compression"),
        subagent_model=config.get("subagent_model"),
        subagent_temperature=config.get("subagent_temperature", 0.1),
        thread_id=os.environ.get("NOCODE_THREAD_ID") or None,
        persistence_config=config,
        proxy=resolve_proxy(config),
        no_proxy=resolve_no_proxy(config),
    )


def _emit(event: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(event, ensure_ascii=False) + "\n")
    sys.stdout.flush()


_stream_task: asyncio.Task | None = None


async def _stream_prompt(agent, prompt: str) -> None:
    try:
        async for event_type, *data in agent.chat(prompt):
            if event_type == "runtime_event":
                payload = data[0] if data else {}
                if isinstance(payload, dict):
                    _emit(payload)
            elif event_type == "text":
                _emit({"type": "text", "delta": data[0]})
            elif event_type == "retry":
                _emit(
                    {
                        "type": "retry",
                        "message": str(data[0]),
                        "attempt": data[1],
                        "max_retries": data[2],
                        "delay": data[3],
                    }
                )
            elif event_type == "tool_start":
                name = data[0]
                args = data[1] if len(data) > 1 else {}
                tool_call_id = data[2] if len(data) > 2 else ""
                _emit(
                    {
                        "type": "tool_start",
                        "name": name,
                        "args": args,
                        "tool_call_id": tool_call_id,
                    }
                )
                if name == "ask_user_question":
                    qs = args.get("questions", [])
                    logger.info("ask_user_question detected, questions=%s", qs)
                    _emit(
                        {
                            "type": "question",
                            "questions": qs,
                            "tool_call_id": tool_call_id,
                        }
                    )
            elif event_type == "tool_end":
                _emit(
                    {
                        "type": "tool_end",
                        "name": data[0],
                        "output": data[1] if len(data) > 1 else "",
                        "tool_call_id": data[2] if len(data) > 2 else "",
                    }
                )
            elif event_type in {"subagent_start", "subagent_tool_start", "subagent_tool_end", "subagent_finish"}:
                payload = data[0] if data else {}
                if isinstance(payload, dict):
                    _emit(payload)
    except asyncio.CancelledError:
        _emit({"type": "cancelled"})
    except Exception as error:
        logger.error("Stream error: %s", error, exc_info=True)
        _emit({"type": "error", "message": f"stream error: {error}"})
    _emit({"type": "done"})


async def _handle_message(agent, payload: dict[str, Any], config: dict[str, Any]) -> bool:
    message_type = payload.get("type")

    if message_type == "clear":
        await agent.clear()
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

    if message_type == "list_threads":
        db_path = resolve_checkpoint_path(config)
        source_filter = str(payload.get("source", "")).strip() or "tui"
        threads = list_threads(db_path, source=source_filter)
        _emit({"type": "thread_list", "threads": threads})
        return True

    if message_type == "resume_thread":
        target_thread = str(payload.get("thread_id", "")).strip()
        if not target_thread:
            _emit({"type": "error", "message": "empty thread_id for resume"})
            return True
        agent._thread_id = target_thread
        _emit(
            {
                "type": "resumed",
                "thread_id": agent.thread_id,
                "model": agent.model_name,
                "subagent_model": agent.subagent_model_name,
                "cwd": os.getcwd(),
            }
        )
        return True

    if message_type == "load_history":
        db_path = resolve_checkpoint_path(config)
        messages = load_thread_messages(db_path, thread_id=agent.thread_id)
        _emit({"type": "history", "messages": messages})
        return True

    if message_type == "exit":
        return False

    _emit({"type": "error", "message": f"unknown message type: {message_type}"})
    return True


async def main() -> int:
    global _stream_task
    setup_logging()
    try:
        config = _load_config()
        agent = await _build_agent(config)
    except Exception as error:
        logger.error("Fatal error during initialization: %s", error)
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
            logger.warning("Invalid JSON on stdin: %s", error)
            _emit({"type": "error", "message": f"invalid json: {error}"})
            continue

        message_type = payload.get("type")

        # ── Prompt: run as background task so stdin stays readable ──
        if message_type == "prompt":
            prompt = str(payload.get("text", "")).strip()
            if not prompt:
                _emit({"type": "error", "message": "empty prompt"})
                continue
            if _stream_task and not _stream_task.done():
                await agent.enqueue_user_input(prompt)
                _emit({"type": "prompt_queued", "text": prompt})
            else:
                _stream_task = asyncio.create_task(_stream_prompt(agent, prompt))
            continue

        if message_type == "question_answer":
            answer = str(payload.get("text", "")).strip()
            if not answer:
                _emit({"type": "error", "message": "empty question answer"})
                continue
            try:
                await agent.submit_question_answer(answer)
            except RuntimeError as error:
                _emit({"type": "error", "message": str(error)})
            continue

        # ── Cancel: interrupt current stream ──────────────────────
        if message_type == "cancel":
            if _stream_task and not _stream_task.done():
                _stream_task.cancel()
            continue

        # ── Other messages: handle synchronously ──────────────────
        try:
            should_continue = await _handle_message(agent, payload, config)
        except Exception as error:
            logger.error("Handler error: %s", error, exc_info=True)
            _emit({"type": "error", "message": f"handler error: {error}"})
            should_continue = True
        if not should_continue:
            break

    # Cleanup pending stream task
    if _stream_task and not _stream_task.done():
        _stream_task.cancel()
        try:
            await _stream_task
        except asyncio.CancelledError:
            pass

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
