"""Authenticated v0.2 management routes. Mutations retain cookie CSRF and operation idempotency."""

from __future__ import annotations

import time
from typing import Literal

from fastapi import Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field, IPvAnyAddress

from .models import GatewayError, IPState, uid
from .quality import QualityPolicy
from .quotas import aggregate_usage
from .rotation import ManualCommit, ManualPreview, RotationPolicy


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GroupCreate(Model):
    group_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9]{1,32}$")
    name: str = Field(min_length=1, max_length=64)
    description: str = Field(default="", max_length=300)
    policy: QualityPolicy = Field(default_factory=QualityPolicy)
    node_ids: list[str] = Field(default_factory=list, max_length=100)
    enabled: bool = True
    auto_replace: bool = False
    daily_limit: int = Field(default=5, ge=0, le=100000)
    round_limit: int = Field(default=10, ge=0, le=100000)
    slot_limit: int = Field(default=3, ge=1, le=100)
    cooldown_seconds: int = Field(default=60, ge=0, le=86400)


class GroupPatch(Model):
    node_ids: list[str] | None = Field(default=None, max_length=100)
    name: str | None = Field(default=None, min_length=1, max_length=64)
    description: str | None = Field(default=None, max_length=300)
    policy: QualityPolicy | None = None
    enabled: bool | None = None
    auto_replace: bool | None = None
    daily_limit: int | None = Field(default=None, ge=0, le=100000)
    round_limit: int | None = Field(default=None, ge=0, le=100000)
    slot_limit: int | None = Field(default=None, ge=1, le=100)
    cooldown_seconds: int | None = Field(default=None, ge=0, le=86400)


class Members(Model):
    node_ids: list[str] = Field(min_length=1, max_length=100)
    remove: bool = False


class Preview(Model):
    node_ids: list[str] = Field(min_length=1, max_length=100)
    policy: QualityPolicy = Field(default_factory=QualityPolicy)


class Check(Model):
    node_ids: list[str] | None = Field(default=None, max_length=100)


class SourceTest(Model):
    ip: IPvAnyAddress


class SourcePatch(Model):
    key: str | None = Field(default=None, max_length=256, repr=False)
    daily_limit: int | None = Field(default=None, ge=0, le=100000)
    tier: Literal["lite", "lookup"] | None = None


def register(app, gateway, auth, operate):
    g = gateway
    deps = [Depends(auth)]
    prefix = "/api/v1"

    options_cache = {}

    @app.get(prefix + "/accounts/{account_id}/replacement-options", dependencies=deps)
    async def replacement_options(account_id: str):
        provider = g.providers.get(account_id)
        if not provider:
            raise GatewayError("NOT_FOUND", "账号不存在", 404)
        old = options_cache.get(account_id)
        if old and old[0] is provider and old[1] > time.time():
            return old[2]
        try:
            import asyncio

            async with asyncio.timeout(12):
                result = await provider.replacement_options()
        except Exception:
            result = {"error": "库存建议暂不可用；可输入参数后以供应商预检结果为准"}
        options_cache[account_id] = (provider, time.time() + 60, result)
        return result

    @app.get(prefix + "/replacements/requests", dependencies=deps)
    async def replacement_requests():
        return {"items": g.rotation.public_requests()}

    @app.post(prefix + "/replacements/preview", dependencies=deps)
    async def replacement_preview(body: ManualPreview, request: Request):
        return await operate(
            request, "replacement_preview", body.model_dump(), lambda: g.rotation.preview(body)
        )

    @app.post(prefix + "/replacements/commit", dependencies=deps)
    async def replacement_commit(body: ManualCommit, request: Request):
        return await operate(
            request, "manual_replacement", body.model_dump(), lambda: g.rotation.commit(body.preview_id)
        )

    @app.post(prefix + "/replacements/requests/{request_id}/cancel", dependencies=deps)
    async def cancel_replacement(request_id: str, request: Request):
        return await operate(request, "replacement_cancel", {}, lambda: g.rotation.cancel(request_id))

    @app.put(prefix + "/groups/{group_id}/rotation", dependencies=deps)
    async def rotation_policy(group_id: str, body: RotationPolicy, request: Request, reset: bool = False):
        return await operate(
            request,
            "rotation_policy",
            {**body.model_dump(), "reset": reset},
            lambda: g.rotation.save(group_id, body, reset),
        )

    @app.get(prefix + "/groups", dependencies=deps)
    async def groups():
        return {"items": g.quality.public_groups()}

    @app.post(prefix + "/groups", dependencies=deps)
    async def create(body: GroupCreate, request: Request):
        values = body.model_dump(exclude={"group_id", "node_ids"})
        return await operate(
            request,
            "group_create",
            body.model_dump(),
            lambda: g.quality.save_group(
                body.group_id or "g" + uid()[:12], values, body.node_ids, create=True
            ),
        )

    @app.post(prefix + "/groups/preview", dependencies=deps)
    async def preview(body: Preview, request: Request):
        return await operate(
            request,
            "quality_preview",
            body.model_dump(),
            lambda: g.quality.run("default", body.node_ids, body.policy.model_dump(), preview=True),
        )

    @app.patch(prefix + "/groups/{group_id}", dependencies=deps)
    async def patch(group_id: str, body: GroupPatch, request: Request):
        return await operate(
            request,
            "group_update",
            body.model_dump(),
            lambda: g.quality.save_group(
                group_id, body.model_dump(exclude_none=True, exclude={"node_ids"}), body.node_ids
            ),
        )

    @app.post(prefix + "/groups/{group_id}/members", dependencies=deps)
    async def members(group_id: str, body: Members, request: Request):
        return await operate(
            request,
            "group_members",
            body.model_dump(),
            lambda: g.quality.members(group_id, body.node_ids, body.remove),
        )

    @app.delete(prefix + "/groups/{group_id}", dependencies=deps)
    async def delete(group_id: str, request: Request):
        return await operate(request, "group_delete", {}, lambda: g.quality.delete(group_id))

    @app.post(prefix + "/groups/{group_id}/check", dependencies=deps)
    async def check(group_id: str, body: Check, request: Request):
        return await operate(
            request, "quality_check", body.model_dump(), lambda: g.quality.run(group_id, body.node_ids)
        )

    @app.post(prefix + "/groups/{group_id}/round", dependencies=deps)
    async def new_round(group_id: str, request: Request):
        async def action():
            async with g.changed:
                g._writable()
                group = g.quality.groups.get(group_id)
                if not group or group_id == "default" or group_id in g.quality.running:
                    raise GatewayError("GROUP_BUSY", "分组不存在、为默认组或正在检测")
                group["round_id"] = uid()
                g.event("quality", "已开启新筛选轮次；每日及席位限额保留", group_id=group_id)
                await g._persist()
            return {"round_id": group["round_id"]}

        return await operate(request, "quality_round", {}, action)

    @app.get(prefix + "/quality/sources", dependencies=deps)
    async def sources():
        return g.quality.sources()

    @app.patch(prefix + "/quality/sources/{source}", dependencies=deps)
    async def source_patch(source: Literal["ipinfo", "abuseipdb"], body: SourcePatch):
        return await g.quality.save_sources(source, body.model_dump(exclude_none=True))

    @app.post(prefix + "/quality/sources/{source}/test", dependencies=deps)
    async def source_test(source: Literal["ipinfo", "abuseipdb"], body: SourceTest):
        if not body.ip.is_global:
            raise GatewayError("INVALID_IP", "仅支持公共 IP 查询", 422)
        result = await g.quality.fetch_source(source, str(body.ip), 300)
        return {"result": result}

    @app.get(prefix + "/metrics", dependencies=deps)
    async def metrics(
        range: Literal["15m", "1h", "6h", "24h", "7d"] = "1h", account_id: str = "", group_id: str = ""
    ):
        return await g.observer.metrics(range, account_id, group_id)

    @app.get(prefix + "/connections", dependencies=deps)
    async def connections(
        request: Request,
        page: int = Query(1, ge=1),
        page_size: int = Query(50, ge=1, le=100),
        active: bool = False,
        since: float | None = None,
        until: float | None = None,
    ):
        filters = dict(request.query_params)
        filters.update(since=since, until=until)
        if active:
            rows = [
                {k: v for k, v in item.items() if not k.startswith("_")}
                for item in g.observer.active.values()
            ]
            for key in ("account_id", "group_id", "protocol", "ip", "worker", "session"):
                if filters.get(key):
                    rows = [r for r in rows if r.get(key) == filters[key]]
            if filters.get("target"):
                rows = [r for r in rows if filters["target"].lower() in r["target"].lower()]
            return {
                "items": rows[(page - 1) * page_size : page * page_size],
                "total": len(rows),
                "page": page,
                "page_size": page_size,
            }
        return await g.observer.records("connection", filters, page, page_size)

    @app.get(prefix + "/probes", dependencies=deps)
    async def probes(
        request: Request,
        page: int = Query(1, ge=1),
        page_size: int = Query(50, ge=1, le=100),
        since: float | None = None,
        until: float | None = None,
    ):
        return await g.observer.records(
            "probe", {**dict(request.query_params), "since": since, "until": until}, page, page_size
        )

    @app.post(prefix + "/nodes/{node_id}/probe", dependencies=deps)
    async def probe(node_id: str, request: Request):
        async def action():
            node = g.nodes.get(node_id)
            if not node or node.used or node.state not in {IPState.FRESH, IPState.QUARANTINED}:
                raise GatewayError("NODE_BUSY", "只读探测仅支持未使用节点，活跃连接不受影响")
            if node_id in g.quality.busy or node_id in g.probing:
                raise GatewayError("NODE_BUSY", "节点正在检测")
            if node.group_id != "default":
                return await g.quality.run(node.group_id, [node_id], allow_replace=False)
            # Explicit diagnostics work with scheduled active probing turned off, and never retire.
            import copy

            token = (node.ip, node.membership_version)
            g.probing.add(node_id)
            try:
                began = time.monotonic()
                ok = await g.probe(copy.copy(node), g.settings.probe_timeout)
                ms = (time.monotonic() - began) * 1000
                g.observer.probe(node, ok, ms)
                async with g.changed:
                    if token != (node.ip, node.membership_version) or node.used:
                        raise GatewayError("STALE_PROBE", "检测期间资源变化，结果未应用")
                    node.checked_at, node.latency_ms = time.time(), round(ms, 2)
                    if ok:
                        node.last_success_at, node.fail_count = time.time(), 0
                    else:
                        node.fail_count += 1
                    node.state = IPState.FRESH if ok and not g.collision(node) else IPState.QUARANTINED
                    node.reason = "只读检测通过" if node.state == IPState.FRESH else "只读检测未通过/冷却重复"
                    await g._persist()
                    g.changed.notify_all()
                return {"healthy": ok, "latency_ms": ms, "replace_calls": 0}
            finally:
                g.probing.discard(node_id)

        return await operate(request, "read_only_probe", {}, action)

    @app.get(prefix + "/nodes/{node_id}/ip-quality", dependencies=deps)
    async def ipquality_report(node_id: str):
        node = g.nodes.get(node_id)
        if not node:
            raise GatewayError("NOT_FOUND", "资源不存在", 404)
        return {
            "report": await g.ipquality.latest(node),
            "running": g.ipquality.running == node_id,
            "busy": g.ipquality.running is not None,
            "node_ip": node.ip,
        }

    @app.post(prefix + "/nodes/{node_id}/ip-quality", dependencies=deps)
    async def run_ipquality(node_id: str, request: Request):
        return await operate(request, "ipquality_report", {}, lambda: g.ipquality.run(node_id))

    @app.get(prefix + "/overview", dependencies=deps)
    async def overview(
        range: Literal["15m", "1h", "6h", "24h", "7d"] = "1h",
        account_id: str = "",
        group_id: str = "",
        probe_target: str = "generate_204",
    ):
        stats = g.stats(account_id, group_id)
        metrics = await g.observer.metrics(range, account_id, group_id)
        nodes = [
            n
            for n in g.nodes.values()
            if n.state != IPState.ARCHIVED
            and (not account_id or n.account_id == account_id)
            and (not group_id or n.group_id == group_id)
        ]
        now = time.time()
        validated = [n for n in nodes if max(n.checked_at or 0, n.last_success_at or 0) >= now - 1800]
        healthy = [
            n
            for n in nodes
            if n.state in {IPState.FRESH, IPState.IN_USE}
            and (n.last_success_at or n.checked_at or 0) >= now - 1800
        ]
        groups = g.quality.public_groups()
        accounts = [a for a in g.public_accounts() if not account_id or a["account_id"] == account_id]
        for account in accounts:
            subset = [n for n in nodes if n.account_id == account["account_id"]]
            account["scoped_total"] = len(subset)
            account["scoped_fresh"] = sum(g._fresh_eligible(n) for n in subset)
        summary = metrics.get("summary", {})
        probes = await g.observer.records(
            "probe",
            {
                "since": now - {"15m": 900, "1h": 3600, "6h": 21600, "24h": 86400, "7d": 604800}[range],
                "account_id": account_id,
                "group_id": group_id,
                "target": probe_target,
            },
            1,
            1000,
        )
        latencies = sorted(p["elapsed_ms"] for p in probes["items"] if p["result"] == "success")
        edges = [100, 300, 500, 1000, 2000, 5000, float("inf")]
        histogram = [
            sum((0 if i == 0 else edges[i - 1]) <= x < edge for x in latencies)
            for i, edge in enumerate(edges)
        ]
        events = [
            e
            for e in reversed(g.events)
            if (not account_id or e.get("account_id") == account_id)
            and (not group_id or e.get("group_id") == group_id)
        ]
        alerts = [
            {
                "id": j.id,
                "kind": j.state,
                "message": j.error or j.reason,
                "at": j.created_at,
                "account_id": j.account_id,
                "group_id": j.group_id,
            }
            for j in g.replacements.values()
            if j.state in {"FAILED", "UNCERTAIN", "BUDGET_BLOCKED"}
            and (not account_id or j.account_id == account_id)
            and (not group_id or j.group_id == group_id)
        ]
        for a in accounts:
            if a["health"]["state"] != "healthy":
                alerts.append(
                    {
                        "id": "account:" + a["account_id"],
                        "kind": "account",
                        "message": a["health"]["message"],
                        "account_id": a["account_id"],
                    }
                )
        if g.observer.error:
            alerts.append({"id": "observability", "kind": "monitoring", "message": g.observer.error})
        return {
            "stats": stats,
            "metrics": metrics,
            "accounts": accounts[:8],
            "supplier_totals": aggregate_usage(accounts),
            "accounts_total": len(accounts),
            "groups": groups,
            "events": events[:6],
            "alerts": alerts[:6],
            "alerts_total": len(alerts),
            "health": {"healthy": len(healthy), "covered": len(validated), "total": len(nodes)},
            "quality": dict(__import__("collections").Counter(g.quality.state(n) for n in nodes)),
            "success_rate": summary.get("successes", 0) / summary["attempts"]
            if summary.get("attempts")
            else None,
            "probes": {
                "sample_count": len(latencies),
                "total_matched": probes["total"],
                "histogram": histogram,
                "p50": latencies[len(latencies) // 2] if latencies else None,
                "p95": latencies[min(len(latencies) - 1, int(len(latencies) * 0.95))] if latencies else None,
                "target": probe_target,
            },
            "observer": g.observer.status(),
            "address": {
                "host": g.config.public_proxy_host,
                "socks_port": g.config.public_socks_port or g.config.socks_port,
                "http_port": g.config.public_http_proxy_port or g.config.proxy_http_port,
            },
            "at": now,
        }
