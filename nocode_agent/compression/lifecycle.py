"""压缩生命周期中间件。

将 Layer 2 / Layer 3 的触发时机统一收口到 LangChain middleware：
  - before_model: auto-compact 检查与消息替换
  - after_model: session memory 提取
  - wrap_tool_call: 工具调用计数与 read 结果追踪
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware import AgentMiddleware, AgentState
from langchain_core.messages import RemoveMessage, ToolMessage
from langgraph.graph.message import REMOVE_ALL_MESSAGES
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.runtime import Runtime
from langgraph.types import Command
from typing_extensions import override

from nocode_agent.compression.auto_compact import AutoCompactor
from nocode_agent.compression.session_memory import SessionMemoryExtractor
from nocode_agent.interactive import InteractiveSessionBroker

logger = logging.getLogger(__name__)


class CompressionLifecycleMiddleware(AgentMiddleware):
    """统一管理压缩相关生命周期。"""

    def __init__(
        self,
        auto_compactor: AutoCompactor | None = None,
        sm_extractor: SessionMemoryExtractor | None = None,
        interactive_broker: InteractiveSessionBroker | None = None,
    ) -> None:
        self._auto_compactor = auto_compactor
        self._sm_extractor = sm_extractor
        self._interactive_broker = interactive_broker

    @override
    async def abefore_model(
        self,
        state: AgentState[Any],
        runtime: Runtime[Any],
    ) -> dict[str, Any] | None:
        """在模型调用前检查并执行 auto-compact。"""
        del runtime
        if not self._auto_compactor:
            return None

        messages = state["messages"]
        if not self._auto_compactor.should_trigger(messages):
            return None

        if self._interactive_broker:
            await self._interactive_broker.emit_event({"type": "auto_compact_start"})

        logger.info("Auto-compact triggered in middleware, generating summary...")
        result = await self._auto_compactor.compact(messages)
        if result is None:
            if self._interactive_broker:
                await self._interactive_broker.emit_event({"type": "auto_compact_failed"})
            logger.warning("Auto-compact failed in middleware, will retry next turn")
            return None

        logger.info(
            "Auto-compact completed in middleware: %d → %d tokens (%d files restored)",
            result.pre_tokens,
            result.post_tokens,
            result.files_restored,
        )

        # 压缩后清空文件状态缓存，避免后续 read 错误复用旧上下文。
        from nocode_agent.file_state import get_file_state_cache

        get_file_state_cache().clear()
        if self._interactive_broker:
            await self._interactive_broker.emit_event(
                {
                    "type": "auto_compact_done",
                    "strategy": result.strategy,
                    "pre_tokens": result.pre_tokens,
                    "post_tokens": result.post_tokens,
                    "files_restored": result.files_restored,
                }
            )
        return {
            "messages": [
                RemoveMessage(id=REMOVE_ALL_MESSAGES),
                *result.messages,
            ]
        }

    @override
    async def aafter_model(
        self,
        state: AgentState[Any],
        runtime: Runtime[Any],
    ) -> dict[str, Any] | None:
        """在模型回复后尝试提取 session memory。"""
        del runtime
        if not self._sm_extractor:
            return None

        await self._sm_extractor.maybe_extract(state["messages"])
        return None

    @override
    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        """在工具调用期间维护压缩所需的辅助状态。"""
        if self._sm_extractor:
            # 这里按工具调用次数计数，和原先 chat() 中的行为保持一致。
            self._sm_extractor.notify_tool_call()

        result = await handler(request)

        if (
            self._auto_compactor
            and isinstance(result, ToolMessage)
            and request.tool_call.get("name") == "read"
        ):
            self._auto_compactor.file_tracker.record_from_tool_message(result)

        return result
