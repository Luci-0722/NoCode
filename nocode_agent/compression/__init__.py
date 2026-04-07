"""上下文压缩体系。

Layer 1 — Microcompact: 按百分比裁剪旧工具结果。
Layer 2 — Session Memory: 后台维护结构化会话笔记，压缩时免费使用。
Layer 3 — Auto-Compact: SM 优先 → LLM 总结压缩 + 压缩后恢复。
"""

from nocode_agent.compression.config import (
    AutoCompactConfig,
    CompressionConfig,
    SessionMemoryConfig,
    build_auto_compact_config,
    build_session_memory_config,
)
from nocode_agent.compression.estimator import estimate_tokens
from nocode_agent.compression.microcompact import (
    ContextCompressor,
    MicrocompactMiddleware,
)
from nocode_agent.compression.auto_compact import (
    AutoCompactor,
    CompactResult,
    FileReadTracker,
)
from nocode_agent.compression.session_memory import (
    SessionMemoryExtractor,
    SessionMemoryCompactor,
)

__all__ = [
    # config
    "CompressionConfig",
    "AutoCompactConfig",
    "SessionMemoryConfig",
    "build_auto_compact_config",
    "build_session_memory_config",
    # estimator
    "estimate_tokens",
    # Layer 1
    "ContextCompressor",
    "MicrocompactMiddleware",
    # Layer 2
    "SessionMemoryExtractor",
    "SessionMemoryCompactor",
    # Layer 3
    "AutoCompactor",
    "CompactResult",
    "FileReadTracker",
]
