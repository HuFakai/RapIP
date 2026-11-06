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
        await self.db.executescript("""
            CREATE TABLE IF NOT EXISTS lifecycle (
                id TEXT PRIMARY KEY, at REAL NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS lifecycle_time ON lifecycle(at DESC, id DESC);
        """)
        await self.db.commit()

    async def load(self) -> dict:
        async with self.db.execute("SELECT payload FROM state WHERE id = 1") as cursor:
            row = await cursor.fetchone()
        return json.loads(row[0]) if row else {}

    async def save(self, value: dict, trace=(), cutoff=None) -> None:
        payload = json.dumps(value, ensure_ascii=False)
        async with self.lock:
            try:
                await self.db.execute("INSERT OR REPLACE INTO state VALUES (1, ?)", (payload,))
                if trace:
                    await self.db.executemany(
                        "INSERT OR IGNORE INTO lifecycle VALUES (?, ?, ?, ?)",
                        [(r["id"], r["at"], r["kind"], json.dumps(r, ensure_ascii=False)) for r in trace],
                    )
                    # Attach the pre-connection issuance to its exact first binding.
                    for r in trace:
                        if r["kind"] not in {"assigned", "reserved"} or not r.get("binding_id"):
                            continue
                        await self.db.execute(
                            """UPDATE lifecycle SET payload=json_set(payload,'$.binding_id',?)
                            WHERE kind='issued' AND json_extract(payload,'$.binding_id') IS NULL
                            AND json_extract(payload,'$.group_id')=?
                            AND json_extract(payload,'$.worker') IS ?
                            AND json_extract(payload,'$.session')=?""",
                            (r["binding_id"], r.get("group_id"), r.get("worker"), r.get("session")),
                        )
                    await self.db.execute(
                        "DELETE FROM lifecycle WHERE id IN (SELECT id FROM lifecycle ORDER BY at DESC, id DESC LIMIT -1 OFFSET 20000)"
                    )
                if cutoff is not None:
                    await self.db.execute("DELETE FROM lifecycle WHERE at < ?", (cutoff,))
                await self.db.commit()
            except BaseException:
                await self.db.rollback()
                raise

    async def close(self) -> None:
        await self.db.close()

    async def history(self, view, filters, offset, size, cutoff):
        clauses, values = ["at >= ?"], [cutoff]
        if view == "links":
            clauses.append("(json_extract(payload,'$.binding_id') IS NOT NULL OR kind='issued')")
        else:
            clauses.append("json_extract(payload,'$.ip') IS NOT NULL")
        for key in ("group_id", "binding_id", "slot_id", "job_id"):
            if filters.get(key) and key == "slot_id":
                clauses.append(
                    "(json_extract(payload,'$.slot_id') = ? OR json_extract(payload,'$.old_slot_id') = ?)"
                )
                values.extend([filters[key]] * 2)
            elif filters.get(key):
                clauses.append(f"json_extract(payload,'$.{key}') = ?")
                values.append(filters[key])
        if filters.get("q"):
            columns = (
                "worker",
                "session",
                "old_session",
                "ip",
                "old_ip",
                "new_ip",
                "job_id",
                "supplier_id",
                "previous_supplier_id",
            )
            clauses.append(
                "(" + " OR ".join(f"lower(json_extract(payload,'$.{key}')) = ?" for key in columns) + ")"
            )
            values.extend([filters["q"].lower().removeprefix("worker-")] * len(columns))
        where = " AND ".join(clauses)
        async with self.lock:
            async with self.db.execute(f"SELECT count(*) FROM lifecycle WHERE {where}", values) as cur:
                total = (await cur.fetchone())[0]
            async with self.db.execute(
                f"SELECT payload FROM lifecycle WHERE {where} ORDER BY at DESC, id DESC LIMIT ? OFFSET ?",
                [*values, size, offset],
            ) as cur:
                items = [json.loads(row[0]) for row in await cur.fetchall()]
        return items, total
