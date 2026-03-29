"""LLM 提供者抽象基类。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, AsyncIterator

from src.core import Message, ToolDefinition


class BaseProvider(ABC):
    """所有 LLM 提供者的抽象基类。

    子类需要实现 chat() 和 chat_stream() 方法，
    以对接不同的模型 API（智谱、Claude、GPT 等）。
    """

    @abstractmethod
    async def chat(
        self,
        messages: list[Message],
        tools: list[ToolDefinition] | None = None,
    ) -> Message:
        """发送消息到 LLM，返回助手响应（可能包含 tool_calls）。"""

    @abstractmethod
    async def chat_stream(
        self,
        messages: list[Message],
        tools: list[ToolDefinition] | None = None,
    ) -> AsyncIterator[str | list[Any]]:
        """流式调用 LLM，逐步返回文本片段或 tool_calls。"""
