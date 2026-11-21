"""Small SQLite key-value cache for JSON responses of external data sources."""

from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from pathlib import Path
from typing import Any

_SCHEMA = """
CREATE TABLE IF NOT EXISTS cache (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    expires_at REAL
)
"""


class SourceCache:
    """Async wrapper around SQLite. ``ttl_s=None`` stores the value without expiry."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._db_path, timeout=5.0)

    def _get_sync(self, key: str) -> Any | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT value, expires_at FROM cache WHERE key = ?", (key,)
            ).fetchone()
        if row is None:
            return None
        value, expires_at = row
        if expires_at is not None and expires_at < time.time():
            return None
        return json.loads(value)

    def _set_sync(self, key: str, value: Any, ttl_s: float | None) -> None:
        expires_at = None if ttl_s is None else time.time() + ttl_s
        payload = json.dumps(value, ensure_ascii=False, default=str)
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO cache (key, value, expires_at) VALUES (?, ?, ?)",
                (key, payload, expires_at),
            )

    async def get(self, key: str) -> Any | None:
        """Return cached JSON value or None when missing or expired."""
        return await asyncio.to_thread(self._get_sync, key)

    async def set(self, key: str, value: Any, ttl_s: float | None) -> None:
        """Store a JSON-serializable value."""
        await asyncio.to_thread(self._set_sync, key, value, ttl_s)