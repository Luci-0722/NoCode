"""压缩配置数据类。

Layer 1 微压缩 (Microcompact):
  两个触发维度（均为相对于模型上下文窗口的百分比):
  ① token 占用百分比 — 估算 token 超过窗口 N% 时触发裁剪
  ② 工具结果条数百分比 — 可压缩工具结果超过窗口对应条数时触发
  两者任一满足即触发裁剪。 裁剪时始终保留最近 keep_recent_tools 条。

Layer 3 自动总结压缩 (Auto-Compact):
  当 token 占用达到窗口更高百分比时，调用 LLM 对历史对话生成总结，
  替换旧消息为摘要，并恢复关键上下文（最近读取的文件等）。
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class AutoCompactConfig:
    """Auto-Compact 配置。

    Attributes:
        enabled: 是否启用自动总结压缩。
        trigger_percent: token 占用达到窗口的此百分比时触发（应高于微压缩的 trigger_percent）。
        reserved_output_tokens: 为总结生成预留的输出空间。
        buffer_tokens: 触发阈值 = effective_window - buffer_tokens。
        max_summary_tokens: 总结生成的最大输出 tokens。
        max_consecutive_failures: 连续失败 N 次后停止尝试（熔断器）。
        max_files_to_restore: 压缩后恢复最近读取的 N 个文件。
        max_tokens_per_file: 每个恢复文件最多保留的 tokens。
        total_file_budget: 所有恢复文件的总 token 预算。
    """

    enabled: bool = True
    trigger_percent: float = 0.80
    reserved_output_tokens: int = 4_096
    buffer_tokens: int = 13_000
    max_summary_tokens: int = 16_000
    max_consecutive_failures: int = 3

    # 压缩后恢复
    max_files_to_restore: int = 5
    max_tokens_per_file: int = 5_000
    total_file_budget: int = 50_000

    @property
    def effective_window(self) -> int:
        """需要从外部注入 context_window 后才能计算。运行时由 AutoCompactor 动态设置。"""
        return 0  # placeholder, 实际值在 AutoCompactor 中基于 context_window 动态计算


@dataclass
class CompressionConfig:
    """Layer 1 微压缩配置。

    Attributes:
        context_window: 模型上下文窗口大小（tokens），由 agent.py 根据 model 动态注入。

        trigger_token_percent: token 占用达到窗口的此百分比时触发裁剪。
            例: 0.60 → 128K 窗口在 ~76800 tokens 时触发。

        trigger_tool_percent: 可压缩工具结果条数达到窗口对应比例时触发。
            换算: context_window * percent / avg_tool_tokens (默认 ~1000)

        keep_recent_tools: 裁剪时始终保留最近 N 条可压缩工具结果。

        compressible_tools: 可被裁剪的工具名称元组。
    """

    context_window: int = 128_000

    trigger_token_percent: float = 0.60
    trigger_tool_percent: float = 0.60
    keep_recent_tools: int = 5

    compressible_tools: tuple[str, ...] = (
        "read",
        "bash",
        "glob",
        "list_dir",
        "grep",
        "write",
        "edit",
        "web_search",
        "web_fetch",
        "delegate_code",
    )

    # ── 动态阈值计算 ──────────────────────────────────────────

    @property
    def trigger_tokens(self) -> int:
        """触发裁剪的 token 阈值（动态计算）。"""
        return int(self.context_window * self.trigger_token_percent)

    @property
    def trigger_tool_count(self) -> int:
        """触发裁剪的工具结果条数阈值（动态计算）。

        换算: context_window * percent / avg_tool_tokens(1000)
        """
        avg_tool_tokens = 1000
        return max(1, int(self.context_window * self.trigger_tool_percent / avg_tool_tokens))

    @classmethod
    def from_yaml(cls, raw: dict, context_window: int = 128_000) -> "CompressionConfig":
        """从 config.yaml 中的 compression 段构建。"""
        return cls(
            context_window=context_window,
            trigger_token_percent=raw.get("trigger_token_percent", 0.60),
            trigger_tool_percent=raw.get("trigger_tool_percent", 0.60),
            keep_recent_tools=raw.get("keep_recent_tools", 5),
            compressible_tools=tuple(
                raw.get(
                    "compressible_tools",
                    (
                        "read", "bash", "glob", "list_dir", "grep",
                        "write", "edit", "web_search", "web_fetch",
                        "delegate_code",
                    ),
                )
            ),
        )


@dataclass
class SessionMemoryConfig:
    """Session Memory (Layer 2) 配置。

    在对话过程中后台维护一份结构化 Markdown 笔记文件。
    压缩时优先使用该笔记替代 LLM 总结——免费且快速。

    Attributes:
        enabled: 是否启用。
        min_tokens_to_init: 上下文达到此 token 数才开始提取。
        min_tokens_between_updates: 两次提取间至少增长的 token 数。
        min_tool_calls_between_updates: 两次提取间至少发生的工具调用次数。
        max_total_tokens: summary.md 总计最多 token 数。
        storage_path: 记忆文件的存储根目录。
    """

    enabled: bool = True
    min_tokens_to_init: int = 10_000
    min_tokens_between_updates: int = 5_000
    min_tool_calls_between_updates: int = 3
    max_total_tokens: int = 12_000
    storage_path: str = "nocode_agent/.state/session-memory"


def build_session_memory_config(
    raw: dict | None,
) -> SessionMemoryConfig | None:
    """从 config.yaml 的 session_memory 段构建配置。返回 None 表示未启用。"""
    if not raw or not raw.get("enabled", True):
        return None
    return SessionMemoryConfig(
        enabled=raw.get("enabled", True),
        min_tokens_to_init=raw.get("min_tokens_to_init", 10_000),
        min_tokens_between_updates=raw.get("min_tokens_between_updates", 5_000),
        min_tool_calls_between_updates=raw.get("min_tool_calls_between_updates", 3),
        max_total_tokens=raw.get("max_total_tokens", 12_000),
        storage_path=raw.get("storage_path", "nocode_agent/.state/session-memory"),
    )


def build_auto_compact_config(
    raw: dict | None,
    context_window: int = 128_000,
) -> AutoCompactConfig | None:
    """从 config.yaml 的 auto_compact 段构建配置。

    返回 None 表示未启用。
    """
    if not raw or not raw.get("enabled", True):
        return None
    return AutoCompactConfig(
        enabled=raw.get("enabled", True),
        trigger_percent=raw.get("trigger_percent", 0.80),
        reserved_output_tokens=raw.get("reserved_output_tokens", 4_096),
        buffer_tokens=raw.get("buffer_tokens", 13_000),
        max_summary_tokens=raw.get("max_summary_tokens", 16_000),
        max_consecutive_failures=raw.get("max_consecutive_failures", 3),
        max_files_to_restore=raw.get("max_files_to_restore", 5),
        max_tokens_per_file=raw.get("max_tokens_per_file", 5_000),
        total_file_budget=raw.get("total_file_budget", 50_000),
    )
