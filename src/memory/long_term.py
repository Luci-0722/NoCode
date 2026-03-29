"""Long-term memory: persistent storage using SQLite."""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class LongTermMemory:
    def __init__(self, db_path: str | Path = "data/memory.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.row_factory = sqlite3.Row
        self._init_tables()

    def _init_tables(self) -> None:
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS facts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                category TEXT NOT NULL DEFAULT 'general',
                key TEXT,
                content TEXT NOT NULL,
                importance REAL DEFAULT 0.5,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                last_accessed TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS conversations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                summary TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS user_preferences (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_facts_category ON facts(category);
            CREATE INDEX IF NOT EXISTS idx_facts_key ON facts(key);
        """)
        self.conn.commit()

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    # --- Facts ---

    def add_fact(self, content: str, category: str = "general",
                 key: str | None = None, importance: float = 0.5) -> int:
        now = self._now()
        cur = self.conn.execute(
            "INSERT INTO facts (category, key, content, importance, created_at, updated_at, last_accessed) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (category, key, content, importance, now, now, now),
        )
        self.conn.commit()
        return cur.lastrowid

    def get_facts(self, category: str | None = None,
                  limit: int = 20) -> list[dict[str, Any]]:
        if category:
            rows = self.conn.execute(
                "SELECT * FROM facts WHERE category = ? ORDER BY importance DESC, updated_at DESC LIMIT ?",
                (category, limit),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM facts ORDER BY importance DESC, updated_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        for row in rows:
            self.conn.execute(
                "UPDATE facts SET last_accessed = ? WHERE id = ?", (self._now(), row["id"])
            )
        self.conn.commit()
        return [dict(row) for row in rows]

    def search_facts(self, keyword: str, limit: int = 10) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM facts WHERE content LIKE ? ORDER BY importance DESC LIMIT ?",
            (f"%{keyword}%", limit),
        ).fetchall()
        return [dict(row) for row in rows]

    def delete_fact(self, fact_id: int) -> bool:
        cur = self.conn.execute("DELETE FROM facts WHERE id = ?", (fact_id,))
        self.conn.commit()
        return cur.rowcount > 0

    # --- Conversation History ---

    def save_message(self, role: str, content: str, summary: str | None = None) -> int:
        cur = self.conn.execute(
            "INSERT INTO conversations (role, content, summary, created_at) VALUES (?, ?, ?, ?)",
            (role, content, summary, self._now()),
        )
        self.conn.commit()
        return cur.lastrowid

    def get_recent_conversations(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM conversations ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(row) for row in reversed(rows)]

    # --- User Preferences ---

    def set_preference(self, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO user_preferences (key, value, updated_at) VALUES (?, ?, ?)",
            (key, json.dumps(value) if not isinstance(value, str) else value, self._now()),
        )
        self.conn.commit()

    def get_preference(self, key: str, default: str | None = None) -> str | None:
        row = self.conn.execute(
            "SELECT value FROM user_preferences WHERE key = ?", (key,)
        ).fetchone()
        if row:
            return row["value"]
        return default

    def get_all_preferences(self) -> dict[str, str]:
        rows = self.conn.execute("SELECT key, value FROM user_preferences").fetchall()
        return {row["key"]: row["value"] for row in rows}

    # --- Context for LLM ---

    def build_context_block(self) -> str:
        """Build a context string from long-term memory to inject into system prompt."""
        parts = []

        facts = self.get_facts(limit=10)
        if facts:
            fact_lines = [f"- [{f['category']}] {f['content']}" for f in facts]
            parts.append("你记住的关于用户的信息:\n" + "\n".join(fact_lines))

        prefs = self.get_all_preferences()
        if prefs:
            pref_lines = [f"- {k}: {v}" for k, v in prefs.items()]
            parts.append("用户偏好:\n" + "\n".join(pref_lines))

        return "\n\n".join(parts) if parts else ""

    def close(self) -> None:
        self.conn.close()
