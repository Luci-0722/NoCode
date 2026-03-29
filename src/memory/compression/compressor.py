"""上下文压缩器：估算 token 数量，并在超限时裁剪消息。

参考 Claude Code 的 compaction 机制：当上下文接近窗口限制时，
对旧的 tool result（特别是文件内容、命令输出等大块文本）进行摘要化处理。
"""

from __future__ import annotations

import logging
from typing import Any

from src.types import Message

logger = logging.getLogger(__name__)

# 需要进行内容裁剪的 tool name 关键词
_TRUNCATABLE_TOOL_NAMES = ("read", "bash", "glob", "grep")

# 默认保留的最近对话轮数（不裁剪）
_RECENT_ROUNDS_TO_KEEP = 5

# 裁剪时保留的头部和尾部行数
_HEAD_LINES = 3
_TAIL_LINES = 3


class ContextCompressor:
    """上下文压缩器：估算 token 数量，并在超限时裁剪消息。

    简单实现策略：
    1. 保留 system 消息
    2. 保留最近 N 轮对话（不裁剪）
    3. 对旧的 tool result 中大块内容进行截断（保留头尾，中间省略）
    4. 如果仍然超限，移除最旧的非 system 消息
    """

    def __init__(
        self,
        max_context_tokens: int = 8000,
        recent_rounds: int = _RECENT_ROUNDS_TO_KEEP,
        head_lines: int = _HEAD_LINES,
        tail_lines: int = _TAIL_LINES,
    ):
        self.max_context_tokens = max_context_tokens
        self.recent_rounds = recent_rounds
        self.head_lines = head_lines
        self.tail_lines = tail_lines

    def estimate_tokens(self, messages: list[Message]) -> int:
        """估算消息列表的总 token 数量。

        使用简单方案：中文约 1.5 字符/token，英文约 4 字符/token。
        混合内容取折中：len(content) // 3 作为保守估算。
        """
        total = 0
        for msg in messages:
            content = msg.content or ""
            if content:
                total += len(content) // 3
            if msg.tool_calls:
                for tc in msg.tool_calls:
                    total += len(tc.arguments) // 3 + 20
        return total

    def compress(self, messages: list[Message], max_tokens: int | None = None) -> list[Message]:
        """对消息列表进行压缩，使其不超过 max_tokens 限制。"""
        if not messages:
            return messages

        limit = max_tokens or self.max_context_tokens
        current_tokens = self.estimate_tokens(messages)

        if current_tokens <= limit:
            return messages

        logger.info("上下文 token 估算: %d, 限制: %d, 需要压缩", current_tokens, limit)

        # 分离 system 消息和非 system 消息
        system_msgs: list[Message] = []
        non_system_msgs: list[Message] = []
        for msg in messages:
            if msg.role == "system":
                system_msgs.append(msg)
            else:
                non_system_msgs.append(msg)

        system_tokens = self.estimate_tokens(system_msgs)
        budget = max(limit - system_tokens, 0)
        recent_start = self._find_recent_rounds_start(non_system_msgs)

        old_msgs = non_system_msgs[:recent_start]
        recent_msgs = non_system_msgs[recent_start:]
        recent_tokens = self.estimate_tokens(recent_msgs)
        old_budget = max(budget - recent_tokens, 0)

        # 第一步：对旧消息中的 tool result 进行截断
        old_msgs = self._truncate_old_tool_results(old_msgs, old_budget)

        total_tokens = system_tokens + self.estimate_tokens(old_msgs) + recent_tokens
        if total_tokens <= limit:
            compressed = system_msgs + old_msgs + recent_msgs
            logger.info("压缩完成(截断 tool result): %d -> %d tokens", current_tokens, total_tokens)
            return compressed

        # 第二步：如果仍然超限，从旧消息中逐步移除最旧的消息
        while old_msgs and (system_tokens + self.estimate_tokens(old_msgs) + recent_tokens) > limit:
            removed = old_msgs.pop(0)
            logger.debug("移除旧消息: role=%s, name=%s, content_len=%d",
                         removed.role, removed.name, len(removed.content or ""))

        compressed = system_msgs + old_msgs + recent_msgs
        final_tokens = self.estimate_tokens(compressed)
        logger.info("压缩完成(移除旧消息): %d -> %d tokens", current_tokens, final_tokens)
        return compressed

    def _find_recent_rounds_start(self, messages: list[Message]) -> int:
        """找到最近 N 轮对话的起始索引。"""
        rounds_found = 0
        for i in range(len(messages) - 1, -1, -1):
            role = messages[i].role
            if hasattr(role, "value"):
                role = role.value
            if role in ("user", "assistant") and not messages[i].tool_calls:
                rounds_found += 1
                if rounds_found >= self.recent_rounds:
                    return i
        return 0

    def _truncate_old_tool_results(self, messages: list[Message], budget: int) -> list[Message]:
        """对旧消息中的 tool result 内容进行截断。"""
        target_budget = int(budget * 0.8)
        result = [self._try_truncate_message(msg) for msg in messages]

        total = self.estimate_tokens(result)
        if total > target_budget:
            result = self._aggressive_truncate(result, target_budget)

        return result

    def _try_truncate_message(self, msg: Message) -> Message:
        """尝试对单个消息进行截断。"""
        role = msg.role
        if hasattr(role, "value"):
            role = role.value

        if role != "tool":
            return msg

        name = msg.name or ""
        content = msg.content or ""

        should_truncate = any(keyword in name.lower() for keyword in _TRUNCATABLE_TOOL_NAMES)
        if not should_truncate or len(content) < 500:
            return msg

        lines = content.split("\n")
        if len(lines) <= self.head_lines + self.tail_lines + 2:
            return msg

        head = lines[: self.head_lines]
        tail = lines[-self.tail_lines :]
        omitted = len(lines) - self.head_lines - self.tail_lines

        truncated = "\n".join(head)
        truncated += f"\n\n... (已省略 {omitted} 行) ...\n\n"
        truncated += "\n".join(tail)

        logger.debug("截断 tool result: name=%s, 原始行数=%d, 截断后行数=%d",
                     name, len(lines), self.head_lines + self.tail_lines + 3)

        return Message(role=msg.role, content=truncated,
                       tool_call_id=msg.tool_call_id, name=msg.name)

    def _aggressive_truncate(self, messages: list[Message], target_tokens: int) -> list[Message]:
        """对消息进行更激进的截断。"""
        result = []
        for msg in messages:
            role = msg.role
            if hasattr(role, "value"):
                role = role.value

            if role == "tool" and msg.name:
                content = msg.content or ""
                lines = content.split("\n")
                if len(lines) > 10:
                    head = lines[:2]
                    tail = lines[-1:]
                    omitted = len(lines) - 3
                    truncated = "\n".join(head)
                    truncated += f"\n\n... (已省略 {omitted} 行) ...\n\n"
                    truncated += "\n".join(tail)
                    msg = Message(role=msg.role, content=truncated,
                                  tool_call_id=msg.tool_call_id, name=msg.name)
            result.append(msg)

        return result
