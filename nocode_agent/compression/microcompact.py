"""Layer 1: 微压缩 — 工具结果裁剪。

策略:
  ① token 占用超过窗口 N% → 触发
  ② 可压缩工具结果超过窗口对应条数 → 触发
  ③ 裁剪: 将旧工具结果内容替换为占位符，保留调用参数
  ④ 保留最近 keep_recent_tools 条完整内容
"""

from __future__ import annotations

import logging
from typing import Any

from langchain.agents import AgentState
from langchain.agents.middleware import before_model
from langchain_core.messages import AIMessage, BaseMessage, SystemMessage, ToolMessage
from langgraph.graph.message import REMOVE_ALL_MESSAGES
from langgraph.runtime import Runtime

from nocode_agent.compression.config import CompressionConfig
from nocode_agent.compression.estimator import estimate_tokens

logger = logging.getLogger(__name__)

PRUNED_HEAD_TAIL_LINES = 2
PRUNED_MAX_CHARS = 500  # 超过此字符数才触发裁剪
PRUNED_HEAD_TAIL_CHARS = 200  # 裁剪后保留首尾各 200 字符
PRUNED_MARKER = "\n...[裁剪]...\n"


def _count_compressible_tool_results(
    messages: list[BaseMessage],
    compressible_tools: set[str],
) -> int:
    """统计消息中可压缩工具结果的总条数。"""
    return sum(
        1
        for msg in messages
        if isinstance(msg, ToolMessage) and msg.name in compressible_tools
    )


def _collect_tool_ids_to_prune(
    messages: list[BaseMessage],
    compressible_tools: set[str],
    keep_recent: int,
) -> set[str]:
    """收集可被裁剪的 ToolMessage 的 tool_call_id。

    从旧到新扫描，对于可压缩工具结果，保留最近 keep_recent 条，其余标记裁剪。
    """
    compressible_ids: list[str] = []
    for msg in messages:
        if isinstance(msg, ToolMessage) and msg.name in compressible_tools:
            compressible_ids.append(msg.tool_call_id)

    if keep_recent >= len(compressible_ids):
        return set()

    cutoff = len(compressible_ids) - keep_recent
    return set(compressible_ids[:cutoff])


def _truncate_content(content: str) -> str:
    """保留工具结果的前后 N 行或首尾字符，中间用标记替换。

    短内容（<=PRUNED_MAX_CHARS）不裁剪。
    标记中包含原始字符数和行数，方便模型判断结果规模。
    """
    if len(content) <= PRUNED_MAX_CHARS:
        return content

    total_chars = len(content)
    total_lines = content.count("\n") + 1
    marker = f"\n...[裁剪 {total_chars} 字符, {total_lines} 行]...\n"

    lines = content.splitlines()

    # 按行保留前后各 N 行
    if len(lines) > PRUNED_HEAD_TAIL_LINES * 2 + 1:
        head = "\n".join(lines[:PRUNED_HEAD_TAIL_LINES])
        tail = "\n".join(lines[-PRUNED_HEAD_TAIL_LINES:])
        return head + marker + tail

    # 行数不多但内容很长，按字符保留首尾
    head = content[:PRUNED_HEAD_TAIL_CHARS]
    tail = content[-PRUNED_HEAD_TAIL_CHARS:]
    return head + marker + tail


def _prune_tool_results(
    messages: list[BaseMessage],
    ids_to_prune: set[str],
) -> list[BaseMessage]:
    """将指定 tool_call_id 的 ToolMessage 内容裁剪（保留前后 N 行）。

    关键设计（与 Claude Code 一致）:
    - ToolMessage 不删除，内容裁剪但保留首尾
    - AIMessage 的 tool_calls 完全不变
    → 模型仍知道调用过什么工具、传了什么参数，还能看到结果的开头和结尾
    """
    result: list[BaseMessage] = []
    for msg in messages:
        if isinstance(msg, ToolMessage) and msg.tool_call_id in ids_to_prune:
            content = msg.content if isinstance(msg.content, str) else str(msg.content)
            truncated = _truncate_content(content)
            msg = msg.model_copy(update={"content": truncated})
        result.append(msg)
    return result


class ContextCompressor:
    """基于百分比的上下文压缩器。"""

    def __init__(self, config: CompressionConfig):
        self.config = config

    def should_trigger(self, messages: list[BaseMessage]) -> bool:
        """判断是否需要触发压缩。"""
        total_tokens = estimate_tokens(messages)
        if total_tokens >= self.config.trigger_tokens:
            return True

        compressible = set(self.config.compressible_tools)
        tool_count = _count_compressible_tool_results(messages, compressible)
        if tool_count >= self.config.trigger_tool_count:
            return True

        return False

    def compress(self, messages: list[BaseMessage]) -> list[BaseMessage]:
        """对消息列表应用压缩策略，返回压缩后的新列表。"""
        if not self.should_trigger(messages):
            return messages

        compressible = set(self.config.compressible_tools)
        keep = self.config.keep_recent_tools

        ids_to_prune = _collect_tool_ids_to_prune(
            messages, compressible, keep,
        )

        if not ids_to_prune:
            return messages

        return _prune_tool_results(messages, ids_to_prune)


class MicrocompactMiddleware:
    """微压缩中间件：将 ContextCompressor 适配为 Middleware 和 LangChain middleware。"""

    def __init__(self, config: CompressionConfig):
        self._compressor = ContextCompressor(config)

    @property
    def config(self) -> CompressionConfig:
        return self._compressor.config

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
            logger.info(
                "Microcompact: pruned %d → %d messages",
                len(messages), len(compressed),
            )
            return {
                "messages": [
                    RemoveMessage(id=REMOVE_ALL_MESSAGES),
                    *compressed,
                ]
            }

        return _compress_before_model
