"""文件状态缓存 — 跟踪已读文件，支持去重和编辑前置验证。

设计参考 Claude Code 的 FileStateCache，但简化为：
- 基于 content hash + mtime 的双重检查
- LRU 淘汰策略（最多 100 条目、25MB）
- 压缩后不清空（hash + mtime 仍然有效）
"""

from __future__ import annotations

import hashlib
import os
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock
from typing import Optional


@dataclass(slots=True)
class FileState:
    """单个文件的缓存状态。"""

    content_hash: str  # 文件内容的 SHA256 前 16 字符
    mtime: float       # os.path.getmtime() 返回值

    def is_mtime_valid(self, path: Path) -> bool:
        """检查文件是否自上次缓存后未被修改。"""
        try:
            return os.path.getmtime(str(path)) == self.mtime
        except OSError:
            return False


class FileStateCache:
    """LRU 缓存，记录已读文件的状态。

    线程安全。key 为 resolved absolute Path 字符串。
    """

    def __init__(self, max_entries: int = 100, max_size_bytes: int = 25 * 1024 * 1024):
        self._cache: OrderedDict[str, FileState] = OrderedDict()
        self._max_entries = max_entries
        self._max_size_bytes = max_size_bytes
        self._total_bytes = 0
        self._lock = Lock()

    @staticmethod
    def _hash(content: str) -> str:
        return hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]

    def get(self, path: Path) -> Optional[FileState]:
        """获取文件缓存状态。命中时移到 LRU 尾部（最近使用）。"""
        key = str(path.resolve())
        with self._lock:
            state = self._cache.get(key)
            if state is not None:
                self._cache.move_to_end(key)
            return state

    def set(self, path: Path, content: str) -> None:
        """记录文件的缓存状态（Read/Edit/Write 成功后调用）。"""
        key = str(path.resolve())
        try:
            mtime = os.path.getmtime(str(path))
        except OSError:
            return
        content_hash = self._hash(content)
        # 估算内存占用（content_hash 16 字节 + mtime 8 字节 + overhead ≈ 200 字节/条目）
        entry_size = len(content) // 100 + 200

        with self._lock:
            # 如果已存在，先移除旧条目的大小
            if key in self._cache:
                self._total_bytes -= entry_size
            # LRU 淘汰
            while self._cache and (len(self._cache) >= self._max_entries or self._total_bytes > self._max_size_bytes):
                self._cache.popitem(last=False)
                self._total_bytes -= 200  # 近似值
            self._cache[key] = FileState(content_hash=content_hash, mtime=mtime)
            self._total_bytes += entry_size

    def has_valid_read(self, path: Path) -> bool:
        """检查文件是否已被读取且未被外部修改。"""
        state = self.get(path)
        if state is None:
            return False
        return state.is_mtime_valid(path)

    def invalidate(self, path: Path) -> None:
        """使某个文件的缓存失效。"""
        key = str(path.resolve())
        with self._lock:
            self._cache.pop(key, None)

    def clear(self) -> None:
        """清空全部缓存。"""
        with self._lock:
            self._cache.clear()
            self._total_bytes = 0


# ── 模块级单例 ────────────────────────────────────────────────────
_cache = FileStateCache()


def get_file_state_cache() -> FileStateCache:
    """获取全局文件状态缓存实例。"""
    return _cache
