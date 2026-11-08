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

    async def save(self, value: dict, trace=(), cutoff=None, trace_limit=20000) -> None:
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
                    "DELETE FROM lifecycle WHERE id IN (SELECT id FROM lifecycle ORDER BY at DESC, id DESC LIMIT -1 OFFSET ?)",
                    (trace_limit,),
                )
                if cutoff is not None:
                    await self.db.execute("DELETE FROM lifecycle WHERE at < ?", (cutoff,))
                await self.db.commit()
            except BaseException:
                await self.db.rollback()
                raise

    async def status(self):
        async with self.lock:
            async with self.db.execute("SELECT count(*) FROM lifecycle") as cursor:
                count = (await cursor.fetchone())[0]
            async with self.db.execute("PRAGMA freelist_count") as cursor:
                free = (await cursor.fetchone())[0]
            async with self.db.execute("PRAGMA page_size") as cursor:
                reusable = free * (await cursor.fetchone())[0]
        return {
            "state_database_bytes": sum(
                p.stat().st_size
                for p in self.directory.glob("gateway.sqlite3*")
                if p.is_file() and not p.name.endswith("-shm")
            ),
            "trace_records": count,
            "state_reusable_bytes": reusable,
        }

    async def close(self) -> None:
        await self.db.close()

    @staticmethod
    def history_filter(view, filters, cutoff):
        clauses, values = ["at >= ?"], [cutoff]
        if view == "links":
            clauses.append("(json_extract(payload,'$.binding_id') IS NOT NULL OR kind='issued')")
        else:
            clauses.append("json_extract(payload,'$.ip') IS NOT NULL")
        for key in ("group_id", "binding_id", "slot_id", "job_id", "worker"):
            if filters.get(key) and key == "slot_id":
                clauses.append(
                    "(json_extract(payload,'$.slot_id') = ? OR json_extract(payload,'$.old_slot_id') = ?)"
                )
                values.extend([filters[key]] * 2)
            elif filters.get(key):
                clauses.append(f"json_extract(payload,'$.{key}') = ?")
                values.append(filters[key])
        if filters.get("ip"):
            clauses.append(
                "(json_extract(payload,'$.ip') = ? OR json_extract(payload,'$.old_ip') = ? "
                "OR json_extract(payload,'$.new_ip') = ? "
                "OR json_extract(payload,'$.slot_id') IN (SELECT value FROM json_each(?)) "
                "OR json_extract(payload,'$.old_slot_id') IN (SELECT value FROM json_each(?)))"
            )
            slots = json.dumps(filters.get("slots", []))
            values.extend([filters["ip"]] * 3 + [slots, slots])
        elif filters.get("q"):
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
        return " AND ".join(clauses), values

    async def history(self, view, filters, offset, size, cutoff):
        where, values = self.history_filter(view, filters, cutoff)
        async with self.lock:
            async with self.db.execute(f"SELECT count(*) FROM lifecycle WHERE {where}", values) as cur:
                total = (await cur.fetchone())[0]
            async with self.db.execute(
                f"SELECT payload FROM lifecycle WHERE {where} ORDER BY at DESC, id DESC LIMIT ? OFFSET ?",
                [*values, size, offset],
            ) as cur:
                items = [json.loads(row[0]) for row in await cur.fetchall()]
        return items, total

    async def ip_lineage(self, ip, group, explicit_slot, known_slots, cutoff):
        # A business switch changes a link's exit; it does not replace one resource with another.
        async with self.lock:
            if not explicit_slot:
                async with self.db.execute(
                    """SELECT DISTINCT CASE WHEN json_extract(payload,'$.old_ip') = ?
                        AND json_extract(payload,'$.old_slot_id') IS NOT NULL
                        THEN json_extract(payload,'$.old_slot_id')
                        ELSE json_extract(payload,'$.slot_id') END AS slot
                    FROM lifecycle WHERE at >= ? AND (? = '' OR json_extract(payload,'$.group_id') = ?)
                    AND (? IN (json_extract(payload,'$.ip'),json_extract(payload,'$.old_ip'))
                         OR (kind='webshare' AND json_extract(payload,'$.new_ip') = ?))""",
                    (ip, cutoff, group, group, ip, ip),
                ) as cur:
                    slots = sorted(set(known_slots) | {r[0] for r in await cur.fetchall() if r[0]})
            else:
                slots = [explicit_slot]
            if not slots:
                return [], []
            async with self.db.execute(
                """SELECT payload FROM lifecycle WHERE at >= ? AND kind='webshare'
                   AND json_extract(payload,'$.slot_id') IN (SELECT value FROM json_each(?))
                   AND json_extract(payload,'$.status')='COMPLETED'
                   AND json_extract(payload,'$.new_ip') IS NOT NULL
                   AND json_extract(payload,'$.ip') IS NOT NULL
                   AND json_extract(payload,'$.ip') <> json_extract(payload,'$.new_ip')
                   ORDER BY at DESC,id DESC""",
                (cutoff, json.dumps(slots)),
            ) as cur:
                # A repeated observation of the same job does not make another transition.
                seen, changes = set(), []
                for (payload,) in await cur.fetchall():
                    event = json.loads(payload)
                    key = event.get("job_id") or event["id"]
                    if key not in seen:
                        seen.add(key)
                        changes.append({k: event[k] for k in ("slot_id", "ip", "new_ip", "at", "group_id")})
        return slots, changes

    async def chains(self, view, filters, offset, size, cutoff, timeline=False, ascending=False):
        # Match filters against individual events, then fetch the entire matched chain.
        where, values = self.history_filter(view, filters, cutoff)
        cte = (
            """WITH records AS MATERIALIZED (
            SELECT id, at, kind, payload,
                json_extract(payload,'$.binding_id') AS binding,
                json_extract(payload,'$.slot_id') AS slot,
                json_extract(payload,'$.ip') AS ip,
                json_extract(payload,'$.generation') AS generation
            FROM lifecycle WHERE at >= ?
        ), predecessors AS (
            SELECT binding, json_extract(payload,'$.old_slot_id') AS old_slot,
                json_extract(payload,'$.old_ip') AS old_ip, generation - 1 AS old_generation,
                max(generation) AS next_generation
            FROM records WHERE kind='switched' AND binding IS NOT NULL
                AND coalesce(json_extract(payload,'$.source'),'') <> 'manual_supplier'
            GROUP BY binding, old_slot, old_ip, old_generation
        ), keyed AS MATERIALIZED (
            SELECT r.id, r.at, r.kind, r.payload,
                coalesce(json_extract(r.payload,'$.chain_id'),
                    CASE WHEN r.binding IS NOT NULL THEN
                        'binding:' || r.binding || ':' || coalesce(p.next_generation,r.generation,1)
                    WHEN r.kind='issued' THEN 'issued:' || r.id
                    WHEN r.slot IS NOT NULL THEN 'slot:' || r.slot
                    ELSE 'event:' || r.id END) AS chain
            FROM records r LEFT JOIN predecessors p
            ON r.kind IN ('webshare','draining') AND r.binding=p.binding
                AND r.slot=p.old_slot AND r.ip=p.old_ip AND r.generation=p.old_generation
        ), matched AS MATERIALIZED (
            SELECT chain FROM keyed WHERE """
            + where
            + """ GROUP BY chain
        ), ranked AS MATERIALIZED (
            SELECT k.chain, min(k.at) AS latest FROM keyed k JOIN matched m ON k.chain=m.chain
            GROUP BY k.chain
        )
        """
        )
        if timeline:
            direction = "ASC" if ascending else "DESC"
            async with self.lock:
                async with self.db.execute(
                    cte + "SELECT count(*) FROM keyed k JOIN matched m ON k.chain=m.chain",
                    [cutoff, *values],
                ) as cur:
                    total = (await cur.fetchone())[0]
                async with self.db.execute(
                    cte
                    + f"""SELECT k.chain,k.payload FROM keyed k JOIN matched m ON k.chain=m.chain
                    ORDER BY k.at {direction},k.id {direction} LIMIT ? OFFSET ?""",
                    [cutoff, *values, size, offset],
                ) as cur:
                    entries = [
                        {**json.loads(payload), "trace_chain": chain}
                        for chain, payload in await cur.fetchall()
                    ]
                context = {}
                if entries:
                    async with self.db.execute(
                        cte
                        + """SELECT k.chain,k.payload FROM keyed k
                        WHERE k.chain IN (SELECT value FROM json_each(?))
                        AND k.kind IN ('switched','session_requested','rotation_requested','in_place_started')
                        ORDER BY k.at DESC""",
                        [cutoff, *values, json.dumps(list({e["trace_chain"] for e in entries}))],
                    ) as cur:
                        for chain, payload in await cur.fetchall():
                            context.setdefault(chain, json.loads(payload))
            for entry in entries:
                anchor = context.get(entry["trace_chain"])
                if anchor:
                    entry["context"] = {
                        k: anchor.get(k)
                        for k in (
                            "ip",
                            "old_ip",
                            "session",
                            "old_session",
                            "worker",
                            "group_id",
                            "source",
                            "country",
                            "old_country",
                        )
                    }
            return entries, total
        async with self.lock:
            async with self.db.execute(cte + "SELECT count(*) FROM ranked", [cutoff, *values]) as cur:
                total = (await cur.fetchone())[0]
            async with self.db.execute(
                cte
                + """SELECT k.chain,k.payload FROM keyed k JOIN (
                    SELECT chain,latest FROM ranked ORDER BY latest DESC,chain DESC LIMIT ? OFFSET ?
                ) selected ON k.chain=selected.chain ORDER BY selected.latest DESC,k.chain DESC,k.at DESC,k.id DESC""",
                [cutoff, *values, size, offset],
            ) as cur:
                rows = {}
                for key, payload in await cur.fetchall():
                    rows.setdefault(key, []).append(json.loads(payload))
        return rows, total
