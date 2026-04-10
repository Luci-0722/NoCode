"""Layer 3: 自动总结压缩 (Auto-Compact)。

当微压缩不足以控制上下文时:
  - 优先: 使用 Session Memory 压缩（免费，不调用 LLM）
  - 回退: 调用 LLM 对整个历史对话生成结构化总结

流程:
  ① 检查是否需要触发 (token 占用 >= 阈值)
  ② 尝试 Session Memory 压缩 → 成功则直接返回
  ③ 回退: 调用 LLM 生成 <analysis> + <summary>
  ④ 后处理: 剥离 <analysis>，提取 <summary>
  ⑤ 压缩后恢复: 重新注入系统提示词 + 最近读取的文件 + 摘要头
  ⑥ 熔断器: 连续失败 N 次后停止尝试
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_openai import ChatOpenAI

from nocode_agent.compression.config import AutoCompactConfig
from nocode_agent.compression.estimator import estimate_tokens
from nocode_agent.compression.prompts import (
    CONTINUATION_INSTRUCTION,
    SUMMARY_SYSTEM_PROMPT,
    SUMMARY_USER_PROMPT,
    format_summary_for_context,
)

logger = logging.getLogger(__name__)

# 恢复的文件记录条目
_MAX_FILE_CHARS = 20_000  # 单个文件最多保留 20K 字符
_TOTAL_FILE_CHARS = 200_000  # 所有文件总计 200K 字符


@dataclass
class FileReadRecord:
    """记录本会话中读取过的文件，用于压缩后恢复。"""
    path: str
    timestamp: float
    char_count: int


@dataclass
class CompactResult:
    """压缩结果。"""
    messages: list[BaseMessage]  # 压缩后的消息列表
    pre_tokens: int
    post_tokens: int
    files_restored: int
    strategy: str


class FileReadTracker:
    """追踪本会话中读取过的文件（由 Read 工具调用后注册）。"""

    def __init__(self, max_records: int = 20):
        self._records: dict[str, FileReadRecord] = {}
        self._max_records = max_records

    def record(self, path: str, char_count: int) -> None:
        """记录一次文件读取。"""
        self._records[path] = FileReadRecord(
            path=path,
            timestamp=time.time(),
            char_count=char_count,
        )
        # 淘汰最旧的
        if len(self._records) > self._max_records:
            oldest = min(self._records, key=lambda k: self._records[k].timestamp)
            del self._records[oldest]

    def record_from_tool_message(self, message: ToolMessage) -> None:
        """从 ToolMessage 中提取文件路径并记录（用于 read 工具）。"""
        content = message.content if isinstance(message.content, str) else ""
        # read 工具返回的内容通常包含文件路径信息
        # 尝试从 content 中提取路径
        path = self._extract_path_from_content(content)
        if path:
            self.record(path, len(content))

    @staticmethod
    def _extract_path_from_content(content: str) -> str | None:
        """从工具结果内容中提取文件路径。"""
        # 匹配常见的文件路径模式
        import re
        # 绝对路径
        m = re.search(r"(?:^|\n)(/[^\s:]+\.\w+)", content)
        if m:
            return m.group(1)
        return None

    def get_recent(self, count: int = 5) -> list[FileReadRecord]:
        """获取最近读取的 N 个文件记录。"""
        sorted_recs = sorted(self._records.values(), key=lambda r: r.timestamp, reverse=True)
        return sorted_recs[:count]


class AutoCompactor:
    """自动总结压缩器。优先 SM 压缩，回退 LLM 压缩。"""

    def __init__(
        self,
        config: AutoCompactConfig,
        context_window: int,
        llm: ChatOpenAI,
        file_tracker: FileReadTracker | None = None,
        sm_extractor: SessionMemoryExtractor | None = None,
    ):
        self._config = config
        self._context_window = context_window
        self._llm = llm
        self._file_tracker = file_tracker or FileReadTracker()
        self._sm_extractor = sm_extractor
        self._sm_compactor = SessionMemoryCompactor() if sm_extractor else None
        self._consecutive_failures = 0

    @property
    def consecutive_failures(self) -> int:
        return self._consecutive_failures

    @property
    def file_tracker(self) -> FileReadTracker:
        """暴露文件读取追踪器，供 middleware 在工具调用后更新。"""
        return self._file_tracker

    @property
    def threshold(self) -> int:
        """触发 auto-compact 的 token 阈值。"""
        effective = self._context_window - self._config.reserved_output_tokens
        buffer_based = effective - self._config.buffer_tokens
        percent_based = int(self._context_window * self._config.trigger_percent)
        # 取两者中更宽松的（更早触发更安全）
        return min(buffer_based, percent_based)

    def should_trigger(self, messages: list[BaseMessage]) -> bool:
        """判断是否需要触发 auto-compact。"""
        if not self._config.enabled:
            return False
        # 熔断器
        if self._consecutive_failures >= self._config.max_consecutive_failures:
            return False
        token_count = estimate_tokens(messages)
        return token_count >= self.threshold

    async def compact(self, messages: list[BaseMessage]) -> CompactResult | None:
        """执行压缩。返回 None 表示压缩失败。"""
        if not self.should_trigger(messages):
            return None

        pre_tokens = estimate_tokens(messages)

        try:
            # ① 优先尝试 Session Memory 压缩（免费）
            if self._sm_extractor and self._sm_compactor:
                memory_content = self._sm_extractor.read_memory()
                sm_result = self._sm_compactor.compact(
                    messages, memory_content, self._context_window,
                )
                if sm_result is not None:
                    # SM 压缩成功，恢复文件
                    self._restore_recent_files(sm_result, messages)
                    post_tokens = estimate_tokens(sm_result)
                    self._consecutive_failures = 0
                    logger.info("Auto-compact: used Session Memory (free)")
                    return CompactResult(
                        messages=sm_result,
                        pre_tokens=pre_tokens,
                        post_tokens=post_tokens,
                        files_restored=self._count_restored_files(sm_result),
                        strategy="session_memory",
                    )

            # ② 回退到 LLM 总结压缩
            summary = await self._generate_summary(messages)
            if not summary:
                self._consecutive_failures += 1
                return None

            # ③ 后处理
            formatted = format_summary_for_context(summary)
            if not formatted:
                self._consecutive_failures += 1
                return None

            # ④ 构建压缩后的消息列表
            new_messages = self._build_post_compact_messages(
                messages, formatted,
            )
            post_tokens = estimate_tokens(new_messages)

            self._consecutive_failures = 0
            return CompactResult(
                messages=new_messages,
                pre_tokens=pre_tokens,
                post_tokens=post_tokens,
                files_restored=self._count_restored_files(new_messages),
                strategy="summary",
            )

        except Exception:
            self._consecutive_failures += 1
            return None

    async def _generate_summary(self, messages: list[BaseMessage]) -> str | None:
        """调用 LLM 生成对话总结。"""
        # 构建总结请求的消息
        summary_messages: list[BaseMessage] = [
            SystemMessage(content=SUMMARY_SYSTEM_PROMPT),
        ]

        # 将原始对话作为上下文注入（跳过系统消息，避免重复）
        for msg in messages:
            if isinstance(msg, SystemMessage):
                continue
            # 截断过长的 ToolMessage 内容（防止总结请求本身超限）
            if isinstance(msg, ToolMessage) and isinstance(msg.content, str):
                if len(msg.content) > 2000:
                    lines = msg.content.splitlines()
                    if len(lines) > 5:
                        truncated = (
                            "\n".join(lines[:2])
                            + f"\n...[截断 {len(msg.content)} 字符, {len(lines)} 行]...\n"
                            + "\n".join(lines[-2:])
                        )
                    else:
                        truncated = msg.content[:1000] + f"\n...[截断 {len(msg.content)} 字符]..."
                    msg = msg.model_copy(update={"content": truncated})
            summary_messages.append(msg)

        # 追加总结指令
        summary_messages.append(HumanMessage(content=SUMMARY_USER_PROMPT))

        # 调用 LLM
        response = await self._llm.ainvoke(summary_messages)
        content = response.content if isinstance(response.content, str) else str(response.content)
        return content if content.strip() else None

    def _build_post_compact_messages(
        self,
        original_messages: list[BaseMessage],
        formatted_summary: str,
    ) -> list[BaseMessage]:
        """构建压缩后的消息列表。

        顺序: 系统提示词 + 摘要 + 恢复的文件 + 继续指令 + 最近几条消息
        """
        result: list[BaseMessage] = []

        # ① 保留系统消息（通常只有第一条）
        for msg in original_messages:
            if isinstance(msg, SystemMessage):
                result.append(msg)
                break  # 只取第一个

        # ② 注入摘要（标记为压缩摘要）
        summary_header = (
            "此会话从之前超出上下文长度的对话继续。以下摘要覆盖了对话的早期部分。\n\n"
            f"{formatted_summary}"
        )
        result.append(HumanMessage(content=summary_header))

        # ③ 恢复最近读取的文件
        self._restore_recent_files(result, original_messages)

        # ④ 注入继续指令
        result.append(HumanMessage(content=CONTINUATION_INSTRUCTION))

        # ⑤ 保留最近几条消息（最多 2 轮 = 用户+AI+工具结果+AI）
        recent = self._extract_recent_messages(original_messages, max_pairs=2)
        result.extend(recent)

        return result

    def _restore_recent_files(
        self,
        result: list[BaseMessage],
        original_messages: list[BaseMessage],
    ) -> None:
        """从 FileReadTracker 恢复最近读取的文件内容。"""
        records = self._file_tracker.get_recent(self._config.max_files_to_restore)
        if not records:
            return

        file_parts: list[str] = []
        used_chars = 0

        for record in records:
            try:
                from pathlib import Path
                path = Path(record.path)
                if not path.exists():
                    continue
                content = path.read_text(encoding="utf-8", errors="replace")
                if len(content) > _MAX_FILE_CHARS:
                    content = content[:_MAX_FILE_CHARS] + "\n\n[文件过长已截断]"
                if used_chars + len(content) > _TOTAL_FILE_CHARS:
                    break
                file_parts.append(f"### 文件: {record.path}\n\n{content}")
                used_chars += len(content)
            except Exception:
                continue

        if file_parts:
            header = "以下为本会话中最近访问的文件内容（压缩后恢复）:\n\n"
            result.append(HumanMessage(content=header + "\n\n".join(file_parts)))

    def _extract_recent_messages(
        self,
        messages: list[BaseMessage],
        max_pairs: int = 2,
    ) -> list[BaseMessage]:
        """提取最近 N 轮消息（用户+AI 为一轮）。

        从后向前扫描，找到最近的 max_pairs 个用户-AI 交互轮次。
        """
        if max_pairs <= 0:
            return []

        # 找到最后一个 HumanMessage 的位置（用户的最近输入）
        last_human_idx = -1
        for i in range(len(messages) - 1, -1, -1):
            if isinstance(messages[i], HumanMessage) and not self._is_meta_message(messages[i]):
                last_human_idx = i
                break

        if last_human_idx < 0:
            return []

        # 从 last_human_idx 向前找 max_pairs 轮
        pair_count = 0
        start_idx = last_human_idx
        for i in range(last_human_idx, -1, -1):
            if isinstance(messages[i], HumanMessage) and not self._is_meta_message(messages[i]):
                pair_count += 1
                start_idx = i
                if pair_count >= max_pairs:
                    break

        return messages[start_idx:]

    @staticmethod
    def _is_meta_message(msg: BaseMessage) -> bool:
        """判断是否是系统注入的元消息（如摘要、恢复文件等）。"""
        if not isinstance(msg, HumanMessage):
            return False
        content = msg.content if isinstance(msg.content, str) else ""
        return (
            content.startswith("此会话从之前超出上下文长度的对话继续")
            or content.startswith("以下为本会话中最近访问的文件")
            or content.startswith("此会话从之前")
        )

    def _count_restored_files(self, messages: list[BaseMessage]) -> int:
        """统计恢复的文件数量。"""
        count = 0
        for msg in messages:
            if isinstance(msg, HumanMessage):
                content = msg.content if isinstance(msg.content, str) else ""
                if content.startswith("### 文件:"):
                    count += content.count("### 文件:")
        return count
