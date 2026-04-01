"""自定义上下文压缩策略（Middleware 模式）。

支持基于 token 阈值的自动压缩，优先删除可压缩工具（如 read、bash）的调用结果。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from langchain.agents import AgentState
from langchain.agents.middleware import before_model
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    RemoveMessage,
    SystemMessage,
    ToolMessage,
)
from langgraph.graph.message import REMOVE_ALL_MESSAGES
from langgraph.runtime import Runtime


# ── Middleware 接口 ──────────────────────────────────────────────


class Middleware(ABC):
    """消息处理中间件基类。

    每次模型调用前，Agent 会依次执行所有 middleware 的 process 方法，
    对消息列表进行变换（如压缩、注入、过滤等）。
    """

    @abstractmethod
    def process(self, messages: list[BaseMessage]) -> list[BaseMessage]:
        """处理消息列表，返回处理后的新列表。"""
        ...


# ── 压缩策略 ────────────────────────────────────────────────────


@dataclass
class CompressionStrategy:
    """压缩策略配置。

    Attributes:
        trigger_tokens: 触发压缩的 token 估算阈值。
        keep_recent: 始终保留的最近消息数量。
        compressible_tools: 可被压缩删除的工具名称列表。
    """

    trigger_tokens: int = 8000
    keep_recent: int = 10
    compressible_tools: tuple[str, ...] = ("read", "bash", "glob", "grep")


def _estimate_tokens(messages: list[BaseMessage]) -> int:
    """保守估算消息列表的 token 数量（中文约 3 字符/token）。"""
    total = 0
    for msg in messages:
        content = getattr(msg, "content", "")
        if isinstance(content, list):
            total += sum(len(str(b)) for b in content)
        else:
            total += len(str(content))
    return total // 3


class ContextCompressor:
    """基于 token 阈值的上下文压缩器。

    策略：
    1. 始终保留系统消息
    2. 始终保留最近 N 条消息
    3. 超过阈值时，从最旧开始删除可压缩工具的结果及对应的 AI 工具调用
    """

    def __init__(self, strategy: CompressionStrategy | None = None):
        self.strategy = strategy or CompressionStrategy()

    def compress(self, messages: list[BaseMessage]) -> list[BaseMessage]:
        """对消息列表应用压缩策略，返回压缩后的新列表。"""
        if _estimate_tokens(messages) <= self.strategy.trigger_tokens:
            return messages

        strategy = self.strategy

        # 分离系统消息和普通消息
        system: list[BaseMessage] = []
        non_system: list[BaseMessage] = []
        for msg in messages:
            if isinstance(msg, SystemMessage):
                system.append(msg)
            else:
                non_system.append(msg)

        # 分离最近消息和旧消息
        keep = strategy.keep_recent
        if len(non_system) <= keep:
            return messages

        recent = non_system[-keep:]
        old = non_system[:-keep]

        # 收集需要删除的 tool_call_id（来自可压缩工具的结果）
        tool_ids_to_remove: set[str] = set()
        for msg in old:
            if isinstance(msg, ToolMessage) and msg.name in strategy.compressible_tools:
                tool_ids_to_remove.add(msg.tool_call_id)

        if not tool_ids_to_remove:
            return messages

        # 重建旧消息：删除目标工具结果和对应的工具调用
        cleaned_old: list[BaseMessage] = []
        for msg in old:
            if isinstance(msg, ToolMessage) and msg.tool_call_id in tool_ids_to_remove:
                continue

            if isinstance(msg, AIMessage) and msg.tool_calls:
                kept_calls = [
                    tc for tc in msg.tool_calls if tc["id"] not in tool_ids_to_remove
                ]
                if kept_calls:
                    msg = msg.model_copy(update={"tool_calls": kept_calls})
                elif not msg.content:
                    continue
                else:
                    msg = msg.model_copy(update={"tool_calls": []})

            cleaned_old.append(msg)

        return system + cleaned_old + recent


class CompressionMiddleware(Middleware):
    """压缩中间件：将 ContextCompressor 适配为 Middleware 接口。"""

    def __init__(self, strategy: CompressionStrategy | None = None):
        self._compressor = ContextCompressor(strategy)

    def process(self, messages: list[BaseMessage]) -> list[BaseMessage]:
        return self._compressor.compress(messages)

    def as_langchain_middleware(self):
        compressor = self._compressor

        @before_model
        def _compress_before_model(
            state: AgentState,
            runtime: Runtime,
        ) -> dict[str, Any] | None:
            del runtime
            messages = state["messages"]
            compressed = compressor.compress(messages)
            if compressed == messages:
                return None
            return {
                "messages": [
                    RemoveMessage(id=REMOVE_ALL_MESSAGES),
                    *compressed,
                ]
            }

        return _compress_before_model
