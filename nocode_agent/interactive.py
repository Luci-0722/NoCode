"""运行中用户输入与提问等待的交互桥接。"""

from __future__ import annotations

import asyncio
from typing import Any

from langchain.agents.middleware import AgentMiddleware


class InteractiveSessionBroker:
    """管理运行中追加输入、提问等待与运行时事件。"""

    def __init__(self) -> None:
        self._pending_inputs: list[str] = []
        self._input_lock = asyncio.Lock()
        self._question_future: asyncio.Future[str] | None = None
        self._question_lock = asyncio.Lock()
        self._events: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

    async def enqueue_user_input(self, text: str) -> None:
        cleaned = text.strip()
        if not cleaned:
            return
        async with self._input_lock:
            self._pending_inputs.append(cleaned)

    async def drain_user_inputs(self) -> list[str]:
        async with self._input_lock:
            drained = list(self._pending_inputs)
            self._pending_inputs.clear()
            return drained

    async def emit_inputs_injected(self, texts: list[str]) -> None:
        if not texts:
            return
        await self._events.put(
            {
                "type": "queued_prompt_injected",
                "texts": texts,
            }
        )

    async def ask_user_question(self, questions: list[dict[str, Any]]) -> str:
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        async with self._question_lock:
            if self._question_future is not None and not self._question_future.done():
                raise RuntimeError("当前已有待回答的问题，请先等待用户完成回答。")
            self._question_future = future

        try:
            return await future
        finally:
            async with self._question_lock:
                if self._question_future is future:
                    self._question_future = None

    async def submit_question_answer(self, answer: str) -> None:
        async with self._question_lock:
            future = self._question_future
            if future is None or future.done():
                raise RuntimeError("当前没有待回答的问题。")
            future.set_result(answer)

    async def drain_events(self) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        while True:
            try:
                events.append(self._events.get_nowait())
            except asyncio.QueueEmpty:
                return events


class PendingUserInputMiddleware(AgentMiddleware):
    """在每次模型调用前注入运行中追加的用户消息。"""

    name = "pending_user_input"

    def __init__(self, broker: InteractiveSessionBroker) -> None:
        self._broker = broker

    async def abefore_model(self, state: dict[str, Any], runtime: Any) -> dict[str, Any] | None:
        texts = await self._broker.drain_user_inputs()
        if not texts:
            return None
        await self._broker.emit_inputs_injected(texts)
        return {
            "messages": [
                {"role": "user", "content": text}
                for text in texts
            ]
        }
