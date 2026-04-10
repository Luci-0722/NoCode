from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from threading import Lock
from typing import Any

import aiosqlite

logger = logging.getLogger(__name__)


def _import_sqlite_saver():
    try:
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

        return AsyncSqliteSaver
    except ImportError:
        raise RuntimeError(
            "missing LangGraph SQLite async checkpointer support. Install the langgraph SQLite checkpoint package first."
        )


class CheckpointerManager:
    def __init__(self, db_path: str) -> None:
        self._db_path = Path(db_path).expanduser()
        self._saver = None
        self._setup_lock: asyncio.Lock | None = None
        self._setup_done = False
        self._lock = Lock()

    @property
    def db_path(self) -> Path:
        return self._db_path

    def get(self):
        with self._lock:
            if self._saver is None:
                self._db_path.parent.mkdir(parents=True, exist_ok=True)
                connection = aiosqlite.connect(str(self._db_path))
                AsyncSqliteSaver = _import_sqlite_saver()
                self._saver = AsyncSqliteSaver(connection)
            return self._saver

    async def ensure_setup(self) -> None:
        saver = self.get()
        if self._setup_done:
            return
        if self._setup_lock is None:
            self._setup_lock = asyncio.Lock()
        async with self._setup_lock:
            if self._setup_done:
                return
            await saver.setup()
            self._setup_done = True
            logger.info("Checkpointer setup complete: %s", self._db_path)

    async def delete_thread(self, thread_id: str) -> None:
        logger.info("Deleting thread: %s", thread_id)
        saver = self.get()
        await self.ensure_setup()
        delete_thread = getattr(saver, "adelete_thread", None)
        if not callable(delete_thread):
            raise RuntimeError("configured checkpointer does not support delete_thread")
        await delete_thread(thread_id)


def resolve_checkpoint_path(config: dict[str, Any] | None = None) -> str:
    resolved = ""
    if config:
        resolved = str(config.get("checkpoint_db_path", "") or "")
    if not resolved:
        resolved = "nocode_agent/.state/langgraph-checkpoints.sqlite"
    return str(Path(resolved).expanduser())


def list_threads(
    db_path: str,
    limit: int = 50,
    source: str | None = None,
) -> list[dict[str, Any]]:
    """List threads from the checkpoint DB with metadata.

    Returns a list of dicts with keys: thread_id, preview, message_count, source.
    Ordered by most recent first.
    Filter by source ("tui" or "multiagent") if provided.
    """
    import sqlite3 as _sqlite3
    from langgraph.checkpoint.sqlite import SqliteSaver

    db_path = str(Path(db_path).expanduser())
    if not Path(db_path).exists():
        return []

    db = _sqlite3.connect(db_path)
    try:
        saver = SqliteSaver(db)
        saver.setup()

        rows = db.execute(
            "SELECT DISTINCT thread_id FROM checkpoints "
            'WHERE checkpoint_ns = "" ORDER BY rowid DESC LIMIT ?',
            (limit,),
        ).fetchall()
        if not rows:
            return []

        results: list[dict[str, Any]] = []
        for (thread_id,) in rows:
            try:
                state = saver.get({"configurable": {"thread_id": thread_id}})
                if not state:
                    continue
                cv = state.get("channel_values", {})
                msgs = cv.get("messages", [])

                # Find first user message for preview
                preview = ""
                for m in msgs:
                    if getattr(m, "type", "") == "human":
                        content = getattr(m, "content", "")
                        preview = content[:80] if isinstance(content, str) else str(content)[:80]
                        break

                # Classify: multiagent threads start with ACP orchestration prefix
                thread_source = (
                    "multiagent"
                    if preview.startswith("\u4f60\u6b63\u5728\u7531\u4e00\u4e2a ACP \u7f16\u6392\u5c42\u8c03\u5ea6\u8fd0\u884c")
                    else "tui"
                )
                results.append({
                    "thread_id": thread_id,
                    "preview": preview or "(empty)",
                    "message_count": len(msgs),
                    "source": thread_source,
                })
            except Exception:
                results.append({
                    "thread_id": thread_id,
                    "preview": "(error)",
                    "message_count": 0,
                    "source": "unknown",
                })

        if source is not None:
            results = [r for r in results if r["source"] == source]
        return results
    finally:
        db.close()


def load_thread_messages(db_path: str, thread_id: str) -> list[dict[str, Any]]:
    """Load thread history from checkpoint state.

    Returns a normalized event list for TUI replay:
    - text message: {"role": "...", "content": "..."}
    - tool record: {"kind": "tool", "name": "...", "args": {...}, "output": "...", "tool_call_id": "..."}
    """
    import sqlite3 as _sqlite3
    from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
    from langgraph.checkpoint.sqlite import SqliteSaver

    db_path = str(Path(db_path).expanduser())
    if not Path(db_path).exists():
        return []

    db = _sqlite3.connect(db_path)
    try:
        saver = SqliteSaver(db)
        saver.setup()
        state = saver.get({"configurable": {"thread_id": thread_id}})
        if not state:
            return []
        cv = state.get("channel_values", {})
        msgs = cv.get("messages", [])

        results: list[dict[str, Any]] = []
        tool_index_by_call_id: dict[str, int] = {}
        for m in msgs:
            if isinstance(m, HumanMessage):
                content = m.content if isinstance(m.content, str) else str(m.content)
                results.append({"role": "user", "content": content})
            elif isinstance(m, AIMessage):
                # Use .text for token-level content, fallback to .content
                content = getattr(m, "text", "") or ""
                if not content:
                    content = m.content if isinstance(m.content, str) else str(m.content)
                if not content.strip():
                    content = ""
                if content:
                    results.append({"role": "assistant", "content": content})

                # AIMessage 上会附带 tool_calls，恢复为工具记录。
                tool_calls = getattr(m, "tool_calls", None) or []
                for call in tool_calls:
                    call_id = str(call.get("id", "") or "")
                    name = str(call.get("name", "") or "")
                    args = call.get("args", {})
                    results.append(
                        {
                            "kind": "tool",
                            "name": name,
                            "args": args if isinstance(args, dict) else {},
                            "output": "",
                            "tool_call_id": call_id,
                        }
                    )
                    if call_id:
                        tool_index_by_call_id[call_id] = len(results) - 1
            elif isinstance(m, SystemMessage):
                content = m.content if isinstance(m.content, str) else str(m.content)
                results.append({"role": "system", "content": content})
            elif isinstance(m, ToolMessage):
                content = m.content if isinstance(m.content, str) else str(m.content)
                tool_call_id = str(getattr(m, "tool_call_id", "") or "")
                name = str(getattr(m, "name", "") or "")
                idx = tool_index_by_call_id.get(tool_call_id)
                if idx is not None:
                    results[idx]["output"] = content
                    if name and not results[idx].get("name"):
                        results[idx]["name"] = name
                else:
                    results.append(
                        {
                            "kind": "tool",
                            "name": name,
                            "args": {},
                            "output": content,
                            "tool_call_id": tool_call_id,
                        }
                    )
        return results
    finally:
        db.close()


def estimate_thread_tokens(db_path: str, thread_id: str) -> int:
    """估算指定线程当前状态占用的 token 数量。"""
    import sqlite3 as _sqlite3
    from langgraph.checkpoint.sqlite import SqliteSaver

    from nocode_agent.compression.estimator import estimate_tokens

    db_path = str(Path(db_path).expanduser())
    if not Path(db_path).exists():
        return 0

    db = _sqlite3.connect(db_path)
    try:
        saver = SqliteSaver(db)
        saver.setup()
        state = saver.get({"configurable": {"thread_id": thread_id}})
        if not state:
            return 0
        cv = state.get("channel_values", {})
        msgs = cv.get("messages", [])
        if not isinstance(msgs, list):
            return 0
        return estimate_tokens(msgs)
    finally:
        db.close()
