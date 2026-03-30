"""自定义上下文压缩策略。

支持基于 token 阈值的自动压缩，优先删除可压缩工具（如 read、bash）的调用结果。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    SystemMessage,
    ToolMessage,
)


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
        """对消息列表应用压缩策略，返回压缩后的新列表。

        注意：此方法不修改原始消息列表，也不影响 checkpoint 中的持久化状态，
        仅在每次模型调用前提供压缩视图。
        """
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
            return messages  # 没有可压缩的旧消息

        recent = non_system[-keep:]
        old = non_system[:-keep]

        # 收集需要删除的 tool_call_id（来自可压缩工具的结果）
        tool_ids_to_remove: set[str] = set()
        for msg in old:
            if isinstance(msg, ToolMessage) and msg.name in strategy.compressible_tools:
                tool_ids_to_remove.add(msg.tool_call_id)

        if not tool_ids_to_remove:
            return messages  # 没有可压缩的工具结果

        # 重建旧消息：删除目标工具结果和对应的工具调用
        cleaned_old: list[BaseMessage] = []
        for msg in old:
            # 跳过被标记删除的工具结果
            if isinstance(msg, ToolMessage) and msg.tool_call_id in tool_ids_to_remove:
                continue

            # 清理 AI 消息中被删除工具的调用记录
            if isinstance(msg, AIMessage) and msg.tool_calls:
                kept_calls = [
                    tc for tc in msg.tool_calls if tc["id"] not in tool_ids_to_remove
                ]
                if kept_calls:
                    msg = msg.model_copy(update={"tool_calls": kept_calls})
                elif not msg.content:
                    continue  # 无内容也无工具调用的空消息，整体删除
                else:
                    msg = msg.model_copy(update={"tool_calls": []})

            cleaned_old.append(msg)

        return system + cleaned_old + recent
