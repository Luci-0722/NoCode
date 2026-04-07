"""Layer 2: 会话记忆压缩 (Session Memory)。

在对话过程中后台维护一份结构化 Markdown 笔记文件 (summary.md)。
压缩时优先使用该笔记替代 LLM 总结——免费且快速。

流程:
  ① 每轮模型回复后检查提取门槛
  ② 满足门槛时调用 LLM 更新 summary.md
  ③ 压缩时读取 summary.md 作为摘要（不调用 LLM）
  ④ 若 summary.md 为空模板 → 回退到 LLM 总结压缩
"""

from __future__ import annotations

import logging
import re
import time
from pathlib import Path

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_openai import ChatOpenAI

from nocode_agent.compression.config import SessionMemoryConfig
from nocode_agent.compression.estimator import estimate_tokens
from nocode_agent.compression.prompts import (
    DEFAULT_SESSION_MEMORY_TEMPLATE,
    SESSION_MEMORY_COMPACT_HEADER,
    SESSION_MEMORY_EMPTY_NOTE,
    SESSION_MEMORY_UPDATE_PROMPT,
)

logger = logging.getLogger(__name__)


class SessionMemoryExtractor:
    """后台会话记忆提取器。

    在每轮模型回复后检查是否满足提取门槛，
    满足时调用 LLM 更新 summary.md 文件。
    """

    def __init__(
        self,
        config: SessionMemoryConfig,
        llm: ChatOpenAI,
        thread_id: str,
    ):
        self._config = config
        self._llm = llm
        self._thread_id = thread_id
        self._memory_path = Path(config.storage_path) / thread_id / "summary.md"
        self._tokens_at_last_extraction: int = 0
        self._tool_calls_since_last: int = 0
        self._extracting = False

    @property
    def memory_path(self) -> Path:
        return self._memory_path

    # ── 提取门槛检查 ──────────────────────────────────────────

    def should_extract(self, messages: list[BaseMessage]) -> bool:
        """判断是否应触发提取。"""
        if self._extracting:
            return False

        total_tokens = estimate_tokens(messages)
        if total_tokens < self._config.min_tokens_to_init:
            return False

        token_growth = total_tokens - self._tokens_at_last_extraction
        if token_growth < self._config.min_tokens_between_updates:
            return False

        # 至少发生过足够的工具调用，或者最后一轮无工具调用（自然对话断点）
        last_ai_has_tools = self._last_ai_has_tool_calls(messages)
        if (
            self._tool_calls_since_last < self._config.min_tool_calls_between_updates
            and last_ai_has_tools
        ):
            return False

        return True

    def notify_tool_call(self) -> None:
        """外部通知发生了一次工具调用。"""
        self._tool_calls_since_last += 1

    # ── 提取执行 ──────────────────────────────────────────────

    async def maybe_extract(self, messages: list[BaseMessage]) -> bool:
        """检查并执行提取（如果满足门槛）。返回是否执行了提取。"""
        if not self.should_extract(messages):
            return False

        self._extracting = True
        try:
            await self._do_extract(messages)
            self._tokens_at_last_extraction = estimate_tokens(messages)
            self._tool_calls_since_last = 0
            return True
        except Exception as e:
            logger.warning("Session memory extraction failed: %s", e)
            return False
        finally:
            self._extracting = False

    async def _do_extract(self, messages: list[BaseMessage]) -> None:
        """调用 LLM 更新 summary.md。"""
        current_notes = self._read_memory_file()

        prompt = SESSION_MEMORY_UPDATE_PROMPT.format(current_notes=current_notes)

        # 构建提取请求: 对话内容 + 更新指令
        extract_messages: list[BaseMessage] = []
        for msg in messages:
            if isinstance(msg, SystemMessage):
                continue
            # 截断过长的 ToolMessage
            if isinstance(msg, ToolMessage) and isinstance(msg.content, str):
                if len(msg.content) > 1000:
                    content = msg.content[:500] + f"\n...[截断 {len(msg.content)} 字符]..."
                    msg = msg.model_copy(update={"content": content})
            extract_messages.append(msg)

        extract_messages.append(HumanMessage(content=prompt))

        response = await self._llm.ainvoke(extract_messages)
        content = response.content if isinstance(response.content, str) else str(response.content)

        if content.strip():
            self._write_memory_file(content.strip())

    # ── 文件 IO ───────────────────────────────────────────────

    def _ensure_memory_dir(self) -> None:
        self._memory_path.parent.mkdir(parents=True, exist_ok=True)

    def _read_memory_file(self) -> str:
        """读取 summary.md，不存在则创建模板。"""
        if not self._memory_path.exists():
            self._ensure_memory_dir()
            self._memory_path.write_text(DEFAULT_SESSION_MEMORY_TEMPLATE, encoding="utf-8")
            return DEFAULT_SESSION_MEMORY_TEMPLATE
        return self._memory_path.read_text(encoding="utf-8", errors="replace")

    def _write_memory_file(self, content: str) -> None:
        self._ensure_memory_dir()
        self._memory_path.write_text(content, encoding="utf-8")

    def read_memory(self) -> str:
        """公开读取接口，供 Compactor 使用。"""
        return self._read_memory_file()

    # ── 辅助 ──────────────────────────────────────────────────

    @staticmethod
    def _last_ai_has_tool_calls(messages: list[BaseMessage]) -> bool:
        """最后一条 AIMessage 是否包含 tool_calls。"""
        for msg in reversed(messages):
            if isinstance(msg, AIMessage):
                return bool(getattr(msg, "tool_calls", None))
        return False


class SessionMemoryCompactor:
    """使用会话记忆进行压缩（不调用 LLM）。

    读取 summary.md 内容作为摘要，
    保留最近的消息，裁剪旧消息。
    若 summary.md 内容为空模板 → 返回 None，回退到 LLM 压缩。
    """

    # 保留最近消息的 token 范围
    MIN_KEEP_TOKENS = 10_000
    MAX_KEEP_TOKENS = 40_000
    MIN_KEEP_MESSAGES = 5

    def compact(
        self,
        messages: list[BaseMessage],
        memory_content: str,
        context_window: int,
    ) -> list[BaseMessage] | None:
        """执行 SM 压缩。返回新消息列表，或 None 表示回退。

        Args:
            messages: 当前对话消息列表。
            memory_content: summary.md 的内容。
            context_window: 模型上下文窗口大小（tokens）。

        Returns:
            压缩后的消息列表，或 None（回退到 LLM 压缩）。
        """
        if self._is_empty_template(memory_content):
            return None

        # 找到保留消息的起始位置
        keep_from = self._calculate_keep_index(
            messages,
            context_window,
        )

        # 构建压缩后消息
        result: list[BaseMessage] = []

        # ① 系统消息
        for msg in messages:
            if isinstance(msg, SystemMessage):
                result.append(msg)
                break

        # ② 会话记忆摘要
        summary_text = SESSION_MEMORY_COMPACT_HEADER + self._truncate_memory(
            memory_content,
        )
        result.append(HumanMessage(content=summary_text))

        # ③ 保留的最近消息（跳过已有的 SystemMessage）
        kept = messages[keep_from:]
        for msg in kept:
            if isinstance(msg, SystemMessage):
                continue
            result.append(msg)

        return result

    # ── 内部方法 ──────────────────────────────────────────────

    @staticmethod
    def _is_empty_template(content: str) -> bool:
        """检查内容是否仍为空模板（所有章节只有描述行）。"""
        non_template_lines = []
        for line in content.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith("# "):
                continue
            if stripped.startswith("_") and stripped.endswith("_"):
                continue
            non_template_lines.append(stripped)
        return len(non_template_lines) <= 2

    def _calculate_keep_index(
        self,
        messages: list[BaseMessage],
        context_window: int,
    ) -> int:
        """计算保留消息的起始索引。

        从消息列表末尾向前扫描，保留满足最低 token 要求的连续段。
        跳过 SystemMessage（它在 ① 中已单独添加）。
        """
        # 跳过开头的 SystemMessage
        start = 0
        for i, msg in enumerate(messages):
            if isinstance(msg, SystemMessage):
                start = i + 1
                break

        # 从末尾向前累积 token
        cumulative = 0
        idx = len(messages)

        for i in range(len(messages) - 1, start - 1, -1):
            msg_tokens = estimate_tokens([messages[i]])
            cumulative += msg_tokens

            if cumulative >= self.MAX_KEEP_TOKENS:
                idx = i + 1
                break
            idx = i

            if cumulative >= self.MIN_KEEP_TOKENS:
                # 检查是否满足最低消息数
                kept_count = len(messages) - idx
                if kept_count >= self.MIN_KEEP_MESSAGES:
                    break

        # 确保不在 tool_use / tool_result 对中间切断
        idx = self._adjust_for_tool_pairs(messages, idx)

        # 不要回溯到 SystemMessage
        if idx < start:
            idx = start

        return idx

    @staticmethod
    def _adjust_for_tool_pairs(
        messages: list[BaseMessage],
        idx: int,
    ) -> int:
        """确保不会在 AIMessage(tool_calls) 和对应的 ToolMessage 之间切断。"""
        if idx >= len(messages):
            return idx

        # 如果 idx 处是 ToolMessage，向前找到对应的 AIMessage
        if idx < len(messages) and isinstance(messages[idx], ToolMessage):
            tool_call_id = getattr(messages[idx], "tool_call_id", None)
            if tool_call_id:
                for i in range(idx - 1, -1, -1):
                    if isinstance(messages[i], AIMessage):
                        calls = getattr(messages[i], "tool_calls", [])
                        if any(c.get("id") == tool_call_id for c in calls):
                            return i
        return idx

    @staticmethod
    def _truncate_memory(content: str) -> str:
        """截断过长的记忆内容（按行截断）。"""
        lines = content.splitlines()
        total_chars = len(content)

        # 约 12000 tokens ≈ 36000 字符（3 chars/token）
        max_chars = 36_000
        if total_chars <= max_chars:
            return content

        kept: list[str] = []
        used = 0
        for line in lines:
            if used + len(line) + 1 > max_chars:
                kept.append("\n[... 截断 ...]")
                break
            kept.append(line)
            used += len(line) + 1
        return "\n".join(kept)
