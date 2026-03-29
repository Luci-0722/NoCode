"""OpenAI 兼容 API 提供者（智谱 GLM 等）。"""

from __future__ import annotations

import logging
from typing import Any, AsyncIterator

from openai import AsyncOpenAI

from src.core import AgentConfig, Message, Role, ToolCall, ToolDefinition
from src.core.provider.base import BaseProvider

logger = logging.getLogger(__name__)


class OpenAICompatibleProvider(BaseProvider):
    """基于 OpenAI SDK 的兼容提供者，适用于智谱 GLM 等兼容 API。"""

    def __init__(self, config: AgentConfig) -> None:
        self.config = config
        self.client = AsyncOpenAI(
            api_key=config.api_key,
            base_url=config.base_url,
        )

    async def chat(
        self,
        messages: list[Message],
        tools: list[ToolDefinition] | None = None,
    ) -> Message:
        """Send messages to LLM, return assistant response (may contain tool_calls)."""
        body: dict[str, Any] = {
            "model": self.config.model,
            "messages": [m.to_openai() for m in messages],
            "max_tokens": self.config.max_tokens,
            "temperature": self.config.temperature,
        }
        if tools:
            body["tools"] = [t.to_openai() for t in tools]

        logger.debug("LLM request: model=%s, messages=%d, tools=%d",
                      self.config.model, len(messages), len(tools or []))

        resp = await self.client.chat.completions.create(**body)
        choice = resp.choices[0]
        msg = choice.message

        tool_calls = None
        if msg.tool_calls:
            tool_calls = [
                ToolCall(
                    id=tc.id,
                    name=tc.function.name,
                    arguments=tc.function.arguments,
                )
                for tc in msg.tool_calls
            ]

        return Message(
            role=Role.ASSISTANT,
            content=msg.content or "",
            tool_calls=tool_calls,
        )

    async def chat_stream(
        self,
        messages: list[Message],
        tools: list[ToolDefinition] | None = None,
    ) -> AsyncIterator[str | list[ToolCall]]:
        """Stream LLM response. Yields text chunks or tool_calls list."""
        body: dict[str, Any] = {
            "model": self.config.model,
            "messages": [m.to_openai() for m in messages],
            "max_tokens": self.config.max_tokens,
            "temperature": self.config.temperature,
            "stream": True,
        }
        if tools:
            body["tools"] = [t.to_openai() for t in tools]

        resp = await self.client.chat.completions.create(**body)
        async for chunk in resp:
            delta = chunk.choices[0].delta
            if delta.content:
                yield delta.content
            if delta.tool_calls:
                tool_calls = []
                for tc in delta.tool_calls:
                    tool_calls.append(ToolCall(
                        id=tc.id or "",
                        name=tc.function.name if tc.function else "",
                        arguments=tc.function.arguments if tc.function else "{}",
                    ))
                yield tool_calls
