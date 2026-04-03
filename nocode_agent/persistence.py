from __future__ import annotations

import asyncio
from pathlib import Path
from threading import Lock
from typing import Any

import aiosqlite


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

    async def delete_thread(self, thread_id: str) -> None:
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
