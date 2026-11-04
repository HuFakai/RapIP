from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import aiosqlite


class Storage:
    """Single-owner durable snapshot. Transactions never interleave on this connection."""

    def __init__(self, directory: Path):
        self.directory = directory
        self.lock = asyncio.Lock()

    async def open(self) -> None:
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.db = await aiosqlite.connect(self.directory / "gateway.sqlite3")
        os.chmod(self.directory / "gateway.sqlite3", 0o600)
        await self.db.execute("PRAGMA journal_mode=WAL")
        await self.db.execute("PRAGMA synchronous=FULL")
        await self.db.execute(
            "CREATE TABLE IF NOT EXISTS state (id INTEGER PRIMARY KEY, payload TEXT NOT NULL)"
        )
        await self.db.commit()

    async def load(self) -> dict:
        async with self.db.execute("SELECT payload FROM state WHERE id = 1") as cursor:
            row = await cursor.fetchone()
        return json.loads(row[0]) if row else {}

    async def save(self, value: dict) -> None:
        payload = json.dumps(value, ensure_ascii=False)
        async with self.lock:
            try:
                await self.db.execute("INSERT OR REPLACE INTO state VALUES (1, ?)", (payload,))
                await self.db.commit()
            except BaseException:
                await self.db.rollback()
                raise

    async def close(self) -> None:
        await self.db.close()
