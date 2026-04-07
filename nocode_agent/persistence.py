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
