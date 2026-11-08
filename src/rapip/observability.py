"""Bounded, best-effort observation. No critical scheduling state lives in this database."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import time
from collections import defaultdict, deque
from pathlib import Path

import aiosqlite

from .models import uid
from .retention import RetentionPolicy

COUNTERS = ("up", "down", "attempts", "successes", "failures", "probes", "probe_ok")
RANGES = {"15m": 900, "1h": 3600, "6h": 21600, "24h": 86400, "7d": 604800}


class Observability:
    def __init__(self, directory: Path):
        self.directory = directory
        self.instance = uid()
        self.queue = deque(maxlen=2000)
        self.counts = defaultdict(lambda: defaultdict(int))
        self.active = {}
        self.mode = "errors"
        self.error = None
        self.dropped = 0
        self.last_tick = time.time()
        self.lock = asyncio.Lock()
        self.last_compact = 0
        self.source_status = {}
        self.retention = RetentionPolicy()
        self.last_cleanup = None
        self.detail_paused = False
        self.noise = {}

    async def open(self):
        try:
            self.db = await aiosqlite.connect(self.directory / "observability.sqlite3")
            os.chmod(self.directory / "observability.sqlite3", 0o600)
            await self.db.executescript("""
                PRAGMA journal_mode=WAL;
                PRAGMA busy_timeout=500;
                CREATE TABLE IF NOT EXISTS samples (
                    bucket INTEGER, resolution INTEGER, scope TEXT, scope_id TEXT,
                    up INTEGER, down INTEGER, attempts INTEGER, successes INTEGER, failures INTEGER,
                    probes INTEGER, probe_ok INTEGER, span REAL, peak INTEGER, gauges TEXT, instance TEXT,
                    PRIMARY KEY(resolution, scope, scope_id, bucket));
                CREATE TABLE IF NOT EXISTS records (
                    id TEXT PRIMARY KEY, kind TEXT, at REAL, account_id TEXT, group_id TEXT,
                    protocol TEXT, result TEXT, ip TEXT, target TEXT, worker TEXT, session TEXT,
                    payload TEXT);
                CREATE INDEX IF NOT EXISTS records_time ON records(kind, at DESC);
                CREATE INDEX IF NOT EXISTS records_scope ON records(group_id, account_id, at DESC);
            """)
            await self.db.commit()
        except Exception:
            self.error = "观测库不可用；业务转发继续，历史数据暂不可用"

    def scopes(self, account="", group=""):
        yield ("global", "")
        if account:
            yield ("account", account)
        if group:
            yield ("group", group)
        if account and group:
            yield ("account_group", account + ":" + group)

    def add(self, key, amount=1, account="", group=""):
        for scope in self.scopes(account, group):
            self.counts[scope][key] += amount

    def begin(self, protocol):
        return {
            "id": uid(),
            "protocol": protocol,
            "started_at": time.time(),
            "at": time.time(),
            "account_id": "",
            "group_id": "",
            "target": "",
            "ip": "",
            "worker": "",
            "session": "",
            "up": 0,
            "down": 0,
            "result": "pending",
            "phase": "connecting",
        }

    def routed(self, item, route):
        item.update(group_id=route.group, worker=route.worker or "", session=route.session)

    def established(self, item, node, binding):
        item.update(
            account_id=node.account_id,
            group_id=node.group_id,
            ip=node.ip,
            binding_id=binding.id,
            generation=binding.generation,
            result="success",
            phase="active",
            established_ms=round((time.time() - item["started_at"]) * 1000, 2),
        )
        self.active[item["id"]] = item
        self._attempt(item)

    def _attempt(self, item):
        if item.get("_counted"):
            return
        item["_counted"] = True
        account, group = item["account_id"], item["group_id"]
        self.add("attempts", account=account, group=group)
        self.add("successes" if item["result"] == "success" else "failures", account=account, group=group)

    def finish(self, item, result="protocol_error", reason="peer_closed"):
        if item.get("_finished"):
            return
        item["_finished"] = True
        self.active.pop(item["id"], None)
        if item["result"] == "pending":
            item["result"] = result
        self._attempt(item)
        item.update(
            phase="closed",
            ended_at=time.time(),
            close_reason=reason,
            duration_ms=round((time.time() - item["started_at"]) * 1000, 2),
        )
        if self.mode == "all" or (
            self.mode == "errors" and (item["result"] != "success" or reason != "peer_closed")
        ):
            record = {k: v for k, v in item.items() if not k.startswith("_")}
            if not any(item.get(k) for k in ("group_id", "target", "ip")) and item["result"] in {
                "authentication_rejected",
                "protocol_error",
            }:
                bucket = int(item["at"] // 60)
                self.noise = {k: v for k, v in self.noise.items() if k[0] >= bucket - 1}
                key = (bucket, item["protocol"], item["result"])
                previous = self.noise.get(key)
                record.update(
                    id=hashlib.sha256(f"{self.instance}:{key}".encode()).hexdigest()[:32],
                    repeat_count=(previous or {}).get("repeat_count", 0) + 1,
                    first_seen=(previous or {}).get("first_seen", item["at"]),
                    last_seen=item["ended_at"],
                    aggregated=True,
                )
                self.noise[key] = record
            self.enqueue("connection", record)

    def enqueue(self, kind, item):
        if self.detail_paused:
            self.dropped += 1
            return
        if len(self.queue) == self.queue.maxlen:
            self.dropped += 1
        self.queue.append((kind, dict(item)))

    def probe(self, node, ok, elapsed_ms, target="generate_204"):
        self.add("probes", account=node.account_id, group=node.group_id)
        self.add("probe_ok", int(ok), account=node.account_id, group=node.group_id)
        self.enqueue(
            "probe",
            {
                "id": uid(),
                "at": time.time(),
                "ip": node.ip,
                "account_id": node.account_id,
                "group_id": node.group_id,
                "result": "success" if ok else "failed",
                "elapsed_ms": round(elapsed_ms, 2),
                "target": target,
                "node_id": node.id,
            },
        )

    def status(self):
        return {
            "mode": self.mode,
            "error": self.error,
            "dropped_records": self.dropped,
            "queued_records": len(self.queue),
            "sample_interval": 5,
            "retention_days": self.retention.metrics_days,
            "retention": self.retention.model_dump(),
            "last_cleanup": self.last_cleanup,
            "detail_paused": self.detail_paused,
            "sources": self.source_status,
        }

    async def flush(self, gateway):
        if not hasattr(self, "db"):
            return
        async with self.lock:
            now = time.time()
            elapsed = min(10, max(0.001, now - self.last_tick))
            discontinuous = now - self.last_tick > 15
            captured = {key: dict(value) for key, value in self.counts.items()}
            dimensions = {
                ("global", ""),
                *[("account", x) for x in gateway.accounts],
                *[("group", x) for x in gateway.quality.groups],
            }
            dimensions.update(
                ("account_group", n.account_id + ":" + n.group_id)
                for n in gateway.nodes.values()
                if n.state != "ARCHIVED"
            )
            dimensions.update(captured)
            records = list(self.queue)
            try:
                for scope, scope_id in dimensions:
                    account, group = (
                        scope_id.split(":", 1)
                        if scope == "account_group"
                        else (scope_id if scope == "account" else "", scope_id if scope == "group" else "")
                    )
                    stats = gateway.stats(account, group)
                    gauges = {
                        k: stats[k]
                        for k in (
                            "total_ips",
                            "fresh_count",
                            "in_use_count",
                            "quarantined_count",
                            "replacing_count",
                            "active_connections",
                            "active_bindings",
                        )
                    }
                    c = captured.get((scope, scope_id), {})
                    await self.db.execute(
                        """INSERT INTO samples VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                        ON CONFLICT(resolution,scope,scope_id,bucket) DO UPDATE SET
                        up=up+excluded.up, down=down+excluded.down, attempts=attempts+excluded.attempts,
                        successes=successes+excluded.successes, failures=failures+excluded.failures,
                        probes=probes+excluded.probes, probe_ok=probe_ok+excluded.probe_ok,
                        span=span+excluded.span, peak=max(peak,excluded.peak), gauges=excluded.gauges,
                        instance=CASE WHEN instance=excluded.instance THEN instance ELSE 'mixed' END""",
                        (
                            int(now // self.retention.metrics_interval_seconds)
                            * self.retention.metrics_interval_seconds,
                            self.retention.metrics_interval_seconds,
                            scope,
                            scope_id,
                            *[c.get(k, 0) for k in COUNTERS],
                            elapsed,
                            gauges["active_connections"],
                            json.dumps(gauges),
                            "mixed" if discontinuous else self.instance,
                        ),
                    )
                for kind, item in records:
                    await self.db.execute(
                        """INSERT INTO records VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                        ON CONFLICT(id) DO UPDATE SET payload=excluded.payload""",
                        (
                            item["id"],
                            kind,
                            item["at"],
                            item.get("account_id", ""),
                            item.get("group_id", ""),
                            item.get("protocol", ""),
                            item.get("result", ""),
                            item.get("ip", ""),
                            item.get("target", ""),
                            item.get("worker", ""),
                            item.get("session", ""),
                            json.dumps(item),
                        ),
                    )
                await self.db.commit()
                for key, values in captured.items():
                    for name, count in values.items():
                        self.counts[key][name] -= count
                ids = {id(x[1]) for x in records}
                self.queue = deque((x for x in self.queue if id(x[1]) not in ids), maxlen=2000)
                self.last_tick = now
                if not self.detail_paused:
                    self.error = None
                if now - self.last_compact > 3600:
                    await self.compact(now)
                    self.last_compact = now
            except Exception:
                with contextlib.suppress(Exception):
                    await self.db.rollback()
                self.error = "观测写入失败；代理继续运行，曲线可能缺测"

    async def compact(self, now):
        for source, target, cutoff in ((10, 60, now - 86400), (30, 60, now - 86400), (60, 900, now - 604800)):
            cutoff = int(cutoff // target) * target
            sums = ",".join(f"SUM({x})" for x in COUNTERS)
            await self.db.execute(
                f"""INSERT INTO samples
                SELECT CAST(s.bucket / ? AS INTEGER)*?, ?, s.scope, s.scope_id, {sums},
                SUM(s.span), MAX(s.peak),
                (SELECT l.gauges FROM samples l WHERE l.scope=s.scope AND l.scope_id=s.scope_id
                 AND l.resolution=? AND l.bucket>=CAST(s.bucket / ? AS INTEGER)*?
                 AND l.bucket<CAST(s.bucket / ? AS INTEGER)*?+? ORDER BY l.bucket DESC LIMIT 1),
                CASE WHEN MIN(s.instance)=MAX(s.instance) THEN MAX(s.instance) ELSE 'mixed' END
                FROM samples s WHERE s.resolution=? AND s.bucket<?
                GROUP BY s.scope,s.scope_id,CAST(s.bucket / ? AS INTEGER)
                ON CONFLICT(resolution,scope,scope_id,bucket) DO UPDATE SET
                up=up+excluded.up, down=down+excluded.down, attempts=attempts+excluded.attempts,
                successes=successes+excluded.successes, failures=failures+excluded.failures,
                probes=probes+excluded.probes, probe_ok=probe_ok+excluded.probe_ok,
                span=span+excluded.span, peak=max(peak,excluded.peak), gauges=excluded.gauges,
                instance=CASE WHEN instance=excluded.instance THEN instance ELSE 'mixed' END""",
                (
                    target,
                    target,
                    target,
                    source,
                    target,
                    target,
                    target,
                    target,
                    target,
                    source,
                    cutoff,
                    target,
                ),
            )
            await self.db.execute("DELETE FROM samples WHERE resolution=? AND bucket<?", (source, cutoff))
        await self.db.execute(
            "DELETE FROM samples WHERE bucket<?", (now - self.retention.metrics_days * 86400,)
        )
        await self.db.execute(
            """DELETE FROM records WHERE
            (kind IN ('probe','ipquality','ping0') AND at<?) OR
            (kind='connection' AND result!='success' AND at<?) OR
            (kind='connection' AND result='success' AND at<?)""",
            (
                now - self.retention.probe_log_days * 86400,
                now - self.retention.error_log_days * 86400,
                now - self.retention.success_log_hours * 3600,
            ),
        )
        await self.db.execute(
            """DELETE FROM records WHERE id IN
            (SELECT id FROM records ORDER BY at DESC LIMIT -1 OFFSET ?)""",
            (self.retention.max_records,),
        )
        await self.db.commit()
        # Stop collecting detail before an unbounded database can exhaust the server disk.
        self.last_cleanup = now
        async with self.db.execute("PRAGMA page_count") as cursor:
            pages = (await cursor.fetchone())[0]
        async with self.db.execute("PRAGMA freelist_count") as cursor:
            free = (await cursor.fetchone())[0]
        async with self.db.execute("PRAGMA page_size") as cursor:
            page_size = (await cursor.fetchone())[0]
        self.detail_paused = (pages - free) * page_size > self.retention.max_database_mb * 1024 * 1024
        if self.detail_paused:
            self.error = f"观测有效数据达到 {self.retention.max_database_mb}MiB 保护阈值，明细采集已暂停"

        elif self.error and "保护阈值" in self.error:
            self.error = None
        await self.db.execute("PRAGMA wal_checkpoint(PASSIVE)")

    async def storage_status(self):
        result = {
            "policy": self.retention.model_dump(),
            "last_cleanup": self.last_cleanup,
            "database_bytes": sum(
                p.stat().st_size
                for p in self.directory.glob("observability.sqlite3*")
                if p.is_file() and not p.name.endswith("-shm")
            ),
            "records": 0,
            "samples": 0,
            "reusable_bytes": 0,
            "error": self.error,
        }
        if not hasattr(self, "db"):
            return result
        async with self.lock:
            for table in ("records", "samples"):
                async with self.db.execute(f"SELECT COUNT(*) FROM {table}") as cursor:
                    result[table] = (await cursor.fetchone())[0]
            async with self.db.execute("SELECT kind, COUNT(*) FROM records GROUP BY kind") as cursor:
                result["record_kinds"] = dict(await cursor.fetchall())
            async with self.db.execute(
                "SELECT resolution, COUNT(*) FROM samples GROUP BY resolution"
            ) as cursor:
                result["sample_resolutions"] = dict(await cursor.fetchall())
            async with self.db.execute("PRAGMA freelist_count") as cursor:
                free = (await cursor.fetchone())[0]
            async with self.db.execute("PRAGMA page_size") as cursor:
                result["reusable_bytes"] = free * (await cursor.fetchone())[0]
        return result

    async def metrics(self, window="1h", account="", group=""):
        seconds = RANGES.get(window, 3600)
        scope, key = ("group", group) if group else (("account", account) if account else ("global", ""))
        if account and group:
            scope, key = "account_group", account + ":" + group
        if not hasattr(self, "db"):
            return {"points": [], "error": self.error, "range": window}
        step = max(self.retention.metrics_interval_seconds, int(seconds / 120 // 10) * 10)
        now = time.time()
        async with self.lock:
            async with self.db.execute(
                """SELECT * FROM samples WHERE scope=? AND scope_id=?
                    AND bucket>=? ORDER BY bucket LIMIT 20000""",
                (scope, key, now - seconds),
            ) as cursor:
                rows = await cursor.fetchall()
        buckets = {}
        for row in rows:
            bucket = int(row[0] // step) * step
            b = buckets.setdefault(
                bucket,
                {
                    "at": bucket,
                    **dict.fromkeys(COUNTERS, 0),
                    "span": 0,
                    "peak": 0,
                    "instance": row[14],
                    "gauges": {},
                },
            )
            for i, name in enumerate(COUNTERS, 4):
                b[name] += row[i]
            b["span"] += row[11]
            b["peak"] = max(b["peak"], row[12])
            b["gauges"] = json.loads(row[13])
            if b["instance"] != row[14]:
                b["instance"] = "mixed"
        points = []
        previous_instance = None
        if buckets:
            for timestamp in range(min(buckets), int(now // step) * step + 1, step):
                b = buckets.get(timestamp)
                if b is None:
                    b = {
                        "at": timestamp,
                        **dict.fromkeys(COUNTERS, 0),
                        "span": 0,
                        "peak": 0,
                        "instance": "missing",
                        "gauges": {},
                    }
                b["up_rate"] = b["up"] / max(0.001, b["span"])
                b["down_rate"] = b["down"] / max(0.001, b["span"])
                b["coverage"] = min(1, b["span"] / step)
                b["gap"] = (
                    b["coverage"] < 0.5
                    or b["instance"] in {"mixed", "missing"}
                    or (previous_instance is not None and b["instance"] != previous_instance)
                )
                if b["instance"] not in {"missing", "mixed"}:
                    previous_instance = b["instance"]
                points.append(b)
        return {
            "points": points,
            "range": window,
            "resolution": step,
            "from": now - seconds,
            "to": now,
            "error": self.error,
            "summary": {k: sum(p[k] for p in points) for k in COUNTERS},
            "peak": max((p["peak"] for p in points), default=0),
        }

    async def records(self, kind, filters, page=1, size=50):
        if not hasattr(self, "db"):
            return {"items": [], "total": 0, "page": page, "page_size": size, "error": self.error}
        clauses, args = ["kind=?"], [kind]
        for key in ("account_id", "group_id", "protocol", "result", "ip", "worker", "session"):
            if value := filters.get(key):
                clauses.append(f"{key}=?")
                args.append(value)
        if filters.get("target"):
            clauses.append("instr(lower(target),lower(?))>0")
            args.append(filters["target"][:253])
        for key, operator in (("since", ">="), ("until", "<=")):
            if filters.get(key) is not None:
                clauses.append(f"at{operator}?")
                args.append(float(filters[key]))
        where = " AND ".join(clauses)
        async with self.lock:
            async with self.db.execute(f"SELECT count(*) FROM records WHERE {where}", args) as cur:
                total = (await cur.fetchone())[0]
            async with self.db.execute(
                f"SELECT payload FROM records WHERE {where} ORDER BY at DESC LIMIT ? OFFSET ?",
                [*args, size, (page - 1) * size],
            ) as cur:
                items = [json.loads(row[0]) for row in await cur.fetchall()]
        return {"items": items, "total": total, "page": page, "page_size": size, "error": self.error}

    async def close(self):
        if hasattr(self, "db"):
            await self.db.close()
