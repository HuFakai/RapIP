"""Project resource ownership and evidence-based admission. All writes use the scheduler lock."""

from __future__ import annotations

import asyncio
import copy
import ipaddress
import json
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Literal

import aiohttp
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .config import private_json
from .models import GatewayError, IPState, uid
from .socks import address_bytes, close_writer, upstream_connect


class QualityPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    samples: int = Field(default=3, ge=1, le=10)
    min_success: int = Field(default=2, ge=1, le=10)
    max_latency_ms: int = Field(default=5000, ge=50, le=30000)
    ttl_hours: int = Field(default=24, ge=1, le=168)
    countries: list[str] = Field(default_factory=list, max_length=50)
    allowed_asns: list[str] = Field(default_factory=list, max_length=100)
    denied_asns: list[str] = Field(default_factory=list, max_length=100)
    network_types: list[Literal["isp", "hosting", "business", "education", "government"]] = Field(
        default_factory=list
    )
    reject_flags: list[Literal["hosting", "tor", "vpn", "proxy", "relay"]] = Field(default_factory=list)
    require_ipinfo: bool = False
    max_abuse_score: int | None = Field(default=None, ge=0, le=100)

    @field_validator("countries")
    @classmethod
    def countries_valid(cls, items):
        if any(len(v) != 2 or not v.isascii() or not v.isalpha() for v in items):
            raise ValueError("国家使用两位 ASCII 代码")
        return sorted(set(v.upper() for v in items))

    @field_validator("allowed_asns", "denied_asns")
    @classmethod
    def asns_valid(cls, items):
        values = [v.upper().removeprefix("AS") for v in items]
        if any(not v.isascii() or not v.isdigit() for v in values):
            raise ValueError("ASN 格式为 AS123 或数字")
        return sorted(set("AS" + v for v in values))

    @model_validator(mode="after")
    def sample_count(self):
        if self.min_success > self.samples:
            raise ValueError("最少成功样本数不能大于总样本数")
        return self


def evaluate(policy: QualityPolicy, evidence: dict) -> dict:
    """Missing required evidence always wins over rejection: never burn IPs on provider errors."""
    failures, unknown, checks = [], [], []
    net = evidence.get("network", {})
    if net.get("error") or not net.get("complete"):
        unknown.append(net.get("error") or "网络检测证据不足")
    else:
        if net.get("successes", 0) < policy.min_success:
            failures.append("连通成功样本不足")
        if net.get("max_latency_ms") is not None and net["max_latency_ms"] > policy.max_latency_ms:
            failures.append("探测耗时超过上限")
        if net.get("exit_match") is not True:
            unknown.append("实际出口与资源 IP 不一致或无法确认")
        checks.append(
            {
                "name": "网络检测",
                "value": f"{net.get('successes', 0)}/{policy.samples}",
                "latency_ms": net.get("max_latency_ms"),
            }
        )
    needs_info = bool(
        policy.require_ipinfo
        or policy.countries
        or policy.allowed_asns
        or policy.denied_asns
        or policy.network_types
        or policy.reject_flags
    )
    info = evidence.get("ipinfo", {})
    if needs_info:
        if info.get("error") or not info:
            unknown.append(info.get("error") or "需要 IPinfo 检测源")
        else:
            for name, allowed in (
                ("country", policy.countries),
                ("asn", policy.allowed_asns),
                ("network_type", policy.network_types),
            ):
                if allowed:
                    if info.get(name) is None:
                        unknown.append(f"检测源未提供 {name}")
                    elif info[name] not in allowed:
                        failures.append(f"{name} 不符合允许范围")
            if policy.denied_asns:
                if info.get("asn") is None:
                    unknown.append("检测源未提供 ASN")
                elif info["asn"] in policy.denied_asns:
                    failures.append("ASN 命中拒绝列表")
            for name in policy.reject_flags:
                flag = info.get("flags", {}).get(name)
                if flag is None:
                    unknown.append(f"当前检测权限未提供 {name}")
                elif flag:
                    failures.append(f"命中拒绝属性 {name}")
            checks.append({"name": "网络属性", "value": info})
    if policy.max_abuse_score is not None:
        abuse = evidence.get("abuseipdb", {})
        score = abuse.get("score")
        if abuse.get("error") or not isinstance(score, int) or isinstance(score, bool):
            unknown.append(abuse.get("error") or "信誉评分不可用")
        elif score > policy.max_abuse_score:
            failures.append("AbuseIPDB 举报置信分超出阈值")
        checks.append({"name": "举报置信分（非业务成功概率）", "value": score})
    return {
        "state": "UNKNOWN" if unknown else ("FAIL" if failures else "PASS"),
        "reasons": unknown + failures,
        "checks": checks,
    }


class QualityManager:
    def __init__(self, gateway):
        self.g = gateway
        self.groups = {
            "default": {
                "id": "default",
                "name": "默认资源",
                "description": "基础健康准入",
                "policy": QualityPolicy().model_dump(),
                "version": 0,
                "enabled": True,
                "auto_replace": False,
                "daily_limit": 5,
                "round_limit": 10,
                "slot_limit": 3,
                "cooldown_seconds": 60,
                "round_id": uid(),
                "created_at": time.time(),
            }
        }
        self.slots = {}
        self.ledger = []
        self.source_usage = {}
        self.source_config = {}
        self.global_daily_limit = 20
        self.busy = set()
        self.running = set()
        self.cache = {}
        self.source_locks = {"ipinfo": asyncio.Lock(), "abuseipdb": asyncio.Lock()}
        self.semaphore = asyncio.Semaphore(3)
        self.source_revision = 0

    def load(self, data):
        self.groups.update(data.get("groups", {}))
        self.slots = data.get("slots", {})
        self.ledger = data.get("ledger", [])
        self.source_usage = data.get("source_usage", {})
        self.global_daily_limit = data.get("global_daily_limit", 20)
        self.source_revision = data.get("source_revision", 0)
        path = self.g.config.config_dir / "quality_sources.json"
        if path.exists():
            self.source_config = json.loads(path.read_text())
        for node in self.g.nodes.values():
            if node.state != IPState.ARCHIVED:
                self.ensure_node(node)
        for job in self.g.replacements.values():
            if node := self.g.nodes.get(job.node_id):
                job.group_id, job.slot_id = node.group_id, node.slot_id

    def snapshot(self):
        return {
            "groups": self.groups,
            "slots": self.slots,
            "ledger": self.ledger,
            "source_usage": self.source_usage,
            "global_daily_limit": self.global_daily_limit,
            "source_revision": self.source_revision,
        }

    def day(self):
        return datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")

    def ensure_node(self, node):
        if not node.slot_id:
            node.slot_id = uid()
        self.slots.setdefault(
            node.slot_id,
            {
                "id": node.slot_id,
                "group_id": node.group_id,
                "node_id": node.id,
                "account_id": node.account_id,
                "streak": 0,
                "last_submit": 0,
            },
        )

    def eligible(self, node):
        group = self.groups.get(node.group_id)
        if not group or not group["enabled"]:
            return False
        if node.group_id == "default":
            return True
        q = node.quality
        return (
            q.get("state") == "PASS"
            and q.get("version") == group["version"]
            and q.get("source_revision") == self.source_revision
            and q.get("expires_at", 0) > time.time()
        )

    def state(self, node):
        if node.group_id == "default":
            return "BASIC"
        q, group = node.quality, self.groups.get(node.group_id, {})
        if not q:
            return "PENDING"
        if (
            q.get("expires_at", 0) <= time.time()
            or q.get("version") != group.get("version")
            or q.get("source_revision") != self.source_revision
        ):
            return "EXPIRED"
        return q.get("state", "UNKNOWN")

    def public_groups(self):
        rows = []
        for group in self.groups.values():
            nodes = [
                n for n in self.g.nodes.values() if n.group_id == group["id"] and n.state != IPState.ARCHIVED
            ]
            states = Counter(self.state(n) for n in nodes)
            used = [x for x in self.ledger if x["group_id"] == group["id"]]
            rows.append(
                {
                    **copy.deepcopy(group),
                    "total": len(nodes),
                    "fresh": sum(self.g._fresh_eligible(n) for n in nodes),
                    "in_use": sum(n.state == IPState.IN_USE for n in nodes),
                    "quality_counts": dict(states),
                    "today_submitted": sum(x["day"] == self.day() for x in used),
                    "round_submitted": sum(x["round_id"] == group["round_id"] for x in used),
                    "running": group["id"] in self.running,
                    "rotation": self.g.rotation.public(group["id"]),
                    "workers": self.g.workers.public(group["id"]),
                    "reserved": sum(n.state == IPState.RESERVED for n in nodes),
                }
            )
        return rows

    def validate_members(self, ids, allow_reserved=False):
        if len(ids) != len(set(ids)):
            raise GatewayError("DUPLICATE_MEMBERS", "成员列表不能重复", 422)
        result = []
        for id in ids:
            node = self.g.nodes.get(id)
            if (
                not node
                or node.state not in {IPState.FRESH, IPState.QUARANTINED}
                or node.used
                or (node.binding_id and not allow_reserved)
            ):
                raise GatewayError("MEMBER_BUSY", "只能选择未使用、无绑定和无在途替换的资源")
            if (
                node.id in self.busy
                or node.id in self.g.probing
                or any(r["node_id"] == node.id for r in self.g.rotation.pending())
            ):
                raise GatewayError("MEMBER_BUSY", "资源正在检测，请稍后再操作")
            if any(
                j.node_id == id and j.state not in {"COMPLETED", "FAILED"}
                for j in self.g.replacements.values()
            ):
                raise GatewayError("MEMBER_BUSY", "资源仍有替换任务")
            result.append(node)
        return result

    async def save_group(self, id, values, node_ids=None, create=False):
        async with self.g.changed:
            self.g._writable()
            if id == "default":
                raise GatewayError("DEFAULT_GROUP", "默认组仅做基础健康准入，不可修改", 422)
            if create == (id in self.groups):
                raise GatewayError("GROUP_CONFLICT", "分组已存在或不存在")
            nodes = self.validate_members(node_ids or [])
            if any(n.group_id not in ("default", id) for n in nodes):
                raise GatewayError("MEMBER_OWNED", "资源已属于其他分组；请先移回默认组")
            group = copy.deepcopy(
                self.groups.get(
                    id,
                    {
                        **self.groups["default"],
                        "id": id,
                        "version": 1,
                        "round_id": uid(),
                        "created_at": time.time(),
                    },
                )
            )
            if "policy" in values:
                policy = QualityPolicy(**values["policy"]).model_dump()
                if not create and group["policy"] != policy:
                    group["version"] += 1
                values = {**values, "policy": policy}
            group.update(values)
            self.groups[id] = group
            if create:
                self.g.workers.rules[id] = {"managed": True, "high_water": len(nodes)}
            for node in nodes:
                self._move(node, id)
            for node in self.g.nodes.values():
                if (
                    node.group_id == id
                    and not node.used
                    and node.state == IPState.FRESH
                    and not self.eligible(node)
                ):
                    node.state, node.reason = IPState.QUARANTINED, "等待本组质量验证"
            self.g.event("quality", "质量分组已保存", group_id=id)
            await self.g._persist()
            self.g.changed.notify_all()
        return next(x for x in self.public_groups() if x["id"] == id)

    def _move(self, node, group):
        self.ensure_node(node)
        node.group_id = group
        node.membership_version += 1
        node.quality = {}
        node.state, node.reason = IPState.QUARANTINED, "分组变更，等待重新准入"
        self.slots[node.slot_id]["group_id"] = group

    async def members(self, id, node_ids, remove=False):
        async with self.g.changed:
            self.g._writable()
            if id not in self.groups or id == "default":
                raise GatewayError("NOT_FOUND", "分组不存在", 404)
            nodes = self.validate_members(node_ids)
            if any(n.group_id != (id if remove else "default") for n in nodes):
                raise GatewayError("MEMBER_OWNED", "请先从原组移出资源")
            for node in nodes:
                self._move(node, "default" if remove else id)
            await self.g._persist()
            self.g.changed.notify_all()
        if remove:
            await self.g._probe_candidates([n.id for n in nodes])
        return {"updated": len(nodes)}

    async def delete(self, id):
        async with self.g.changed:
            self.g._writable()
            if id == "default" or id not in self.groups:
                raise GatewayError("GROUP_CONFLICT", "默认组不能删除或分组不存在")
            if self.g.rotation.pending(group=id):
                raise GatewayError("GROUP_BUSY", "分组仍有预留替换任务，请先取消或完成")
            if id in self.running or any(
                not b.released and b.group_id == id for b in self.g.bindings.values()
            ):
                raise GatewayError("GROUP_BUSY", "分组仍在使用或检测中")
            if any(
                j.group_id == id and j.state not in {"COMPLETED", "FAILED"}
                for j in self.g.replacements.values()
            ):
                raise GatewayError("GROUP_BUSY", "分组仍有替换任务")
            nodes = self.validate_members(
                [n.id for n in self.g.nodes.values() if n.group_id == id and n.state != IPState.ARCHIVED]
            )
            for node in nodes:
                self._move(node, "default")
            del self.groups[id]
            await self.g._persist()
            self.g.changed.notify_all()
        await self.g._probe_candidates([n.id for n in nodes])
        return {"deleted": id}

    def sources(self):
        day = self.day()
        return {
            "items": [
                {
                    "id": name,
                    "configured": bool(self.source_config.get(name, {}).get("key")),
                    "daily_limit": self.source_config.get(name, {}).get("daily_limit", 100),
                    "tier": self.source_config.get(name, {}).get("tier", "lite"),
                    "used_today": self.source_usage.get(f"{day}:{name}", 0),
                    "status": self.g.observer.source_status.get(name),
                    "capabilities": "地区/ASN；其他字段按实际套餐"
                    if name == "ipinfo"
                    else "举报置信分（查询）",
                }
                for name in ("ipinfo", "abuseipdb")
            ],
            "global_daily_limit": self.global_daily_limit,
            "global_used_today": sum(x["day"] == day for x in self.ledger),
        }

    async def save_sources(self, name, values):
        async with self.g.changed:
            self.g._writable()
            config = {**self.source_config.get(name, {}), **values}
            self.source_config[name] = config
            private_json(self.g.config.config_dir / "quality_sources.json", self.source_config)
            self.g.observer.source_status.pop(name, None)
            self.source_revision += 1
            self.cache.clear()
            await self.g._persist()
        return self.sources()

    async def fetch_source(self, name, ip, ttl):
        async with self.source_locks[name]:
            settings = self.source_config.get(name, {})
            if not settings.get("key"):
                return {"error": f"{name} 尚未配置 Key"}
            key = (name, ip, self.source_revision)
            if key in self.cache and self.cache[key]["at"] + ttl > time.time():
                return copy.deepcopy(self.cache[key]["value"])
            async with self.g.changed:
                daily_key = f"{self.day()}:{name}"
                if self.source_usage.get(daily_key, 0) >= settings.get("daily_limit", 100):
                    return {"error": f"{name} 本地每日查询额度已用尽"}
                self.source_usage[daily_key] = self.source_usage.get(daily_key, 0) + 1
                await self.g._persist()
            if name == "ipinfo":
                path = "lite" if settings.get("tier", "lite") == "lite" else "lookup"
                url, headers, params = (
                    f"https://api.ipinfo.io/{path}/{ip}",
                    {"Authorization": "Bearer " + settings["key"]},
                    {},
                )
            else:
                url, headers, params = (
                    "https://api.abuseipdb.com/api/v2/check",
                    {"Key": settings["key"], "Accept": "application/json"},
                    {"ipAddress": ip, "maxAgeInDays": 90},
                )
            try:
                async with self.g.client.get(
                    url,
                    headers=headers,
                    params=params,
                    timeout=aiohttp.ClientTimeout(total=10),
                    allow_redirects=False,
                ) as r:
                    if r.status != 200:
                        raise ValueError(f"检测服务 HTTP {r.status}，未视为 IP 不合格")
                    raw = await r.json()
                if name == "ipinfo":
                    if raw.get("ip") != ip:
                        raise ValueError("检测源未返回对应 IP")
                    anonymous = raw.get("anonymous", raw.get("privacy", {}))
                    flags = {
                        n: raw.get("is_" + n, anonymous.get("is_" + n, anonymous.get(n)))
                        for n in ("hosting", "tor", "proxy", "vpn", "relay")
                    }
                    flags = {k: v for k, v in flags.items() if isinstance(v, bool)}
                    value = {
                        "country": raw.get("geo", {}).get(
                            "country_code", raw.get("country_code", raw.get("country"))
                        ),
                        "asn": raw.get("as", {}).get("asn", raw.get("asn")),
                        "network_type": raw.get("as", {}).get("type"),
                        "operator": raw.get("as", {}).get("name", raw.get("as_name")),
                        "flags": flags,
                    }
                else:
                    data = raw.get("data", {})
                    if data.get("ipAddress") != ip:
                        raise ValueError("信誉源未返回对应 IP")
                    score = data.get("abuseConfidenceScore")
                    if type(score) is not int or not 0 <= score <= 100:
                        raise ValueError("信誉字段不完整")
                    value = {
                        "score": score,
                        "reports": data.get("totalReports"),
                        "last_reported": data.get("lastReportedAt"),
                    }
                value.update(source=name, checked_at=time.time())
                self.cache[key] = {"at": time.time(), "value": value}
                if len(self.cache) > 5000:
                    self.cache.pop(next(iter(self.cache)))
                self.g.observer.source_status[name] = {"state": "ok", "checked_at": time.time()}
                return copy.deepcopy(value)
            except Exception:
                value = {"error": f"{name} 请求失败、无权限、限流或返回字段异常"}
                self.g.observer.source_status[name] = {"state": "error", "checked_at": time.time(), **value}
                return value

    async def network_check(self, node, policy):
        latencies, exits = [], []
        for _ in range(policy.samples):
            started, writer = time.monotonic(), None
            ok = False
            try:
                reader, writer, _ = await upstream_connect(
                    node,
                    address_bytes("api.ipify.org", 443),
                    self.g.settings.probe_timeout,
                    self.g.config.egress_proxy,
                )
                import ssl

                async with asyncio.timeout(self.g.settings.probe_timeout):
                    await writer.start_tls(ssl.create_default_context(), server_hostname="api.ipify.org")
                    writer.write(b"GET / HTTP/1.1\r\nHost: api.ipify.org\r\nConnection: close\r\n\r\n")
                    await writer.drain()
                    response = await reader.read(16384)
                    while len(response) < 16384 and (part := await reader.read(16384 - len(response))):
                        response += part
                    head, body = response.split(b"\r\n\r\n", 1)
                    if not head.startswith(b"HTTP/1.1 200"):
                        raise ValueError("echo failed")
                    # The service returns plain IP text; tolerate HTTP chunking explicitly.
                    if b"transfer-encoding: chunked" in head.lower():
                        output = b""
                        while body:
                            size, body = body.split(b"\r\n", 1)
                            n = int(size.split(b";")[0], 16)
                            if not n:
                                break
                            output += body[:n]
                            body = body[n + 2 :]
                        body = output
                    exits.append(str(ipaddress.ip_address(body.strip().decode())))
                    ok = True
                    latencies.append((time.monotonic() - started) * 1000)
            except Exception:
                pass
            finally:
                await close_writer(writer)
            self.g.observer.probe(node, ok, (time.monotonic() - started) * 1000, "HTTPS api.ipify.org")
        return {
            "complete": True,
            "successes": len(exits),
            "samples": policy.samples,
            "max_latency_ms": round(max(latencies), 2) if latencies else None,
            "exits": sorted(set(exits)),
            "exit_match": bool(exits) and all(x == node.ip for x in exits),
        }

    async def evidence(self, node, policy):
        async with self.semaphore:
            data = {"network": await self.network_check(copy.copy(node), policy)}
            if (
                policy.require_ipinfo
                or policy.countries
                or policy.allowed_asns
                or policy.denied_asns
                or policy.network_types
                or policy.reject_flags
            ):
                data["ipinfo"] = await self.fetch_source("ipinfo", node.ip, policy.ttl_hours * 3600)
            if policy.max_abuse_score is not None:
                data["abuseipdb"] = await self.fetch_source("abuseipdb", node.ip, policy.ttl_hours * 3600)
            return data

    async def run(self, group_id, node_ids=None, policy_override=None, preview=False, allow_replace=True):
        group = self.groups.get(group_id)
        if not group:
            raise GatewayError("NOT_FOUND", "分组不存在", 404)
        if not preview and (group_id == "default" or not group["enabled"]):
            raise GatewayError("GROUP_DISABLED", "请选择已启用的质量组")
        lock_key = group_id if not preview else "preview"
        if lock_key in self.running:
            raise GatewayError("QUALITY_BUSY", "同一分组正在筛查")
        self.running.add(lock_key)
        try:
            async with self.g.changed:
                self.g._writable()
                if node_ids is not None:
                    nodes = self.validate_members(node_ids, allow_reserved=not preview)
                    if not preview and any(n.group_id != group_id for n in nodes):
                        raise GatewayError("GROUP_CONFLICT", "只能检测该组成员")
                else:
                    nodes = [
                        n
                        for n in self.g.nodes.values()
                        if n.group_id == group_id
                        and n.state in {IPState.FRESH, IPState.QUARANTINED}
                        and not n.used
                        and n.id not in self.busy
                        and n.id not in self.g.probing
                    ][:100]
                self.busy.update(n.id for n in nodes)
                tokens = {
                    n.id: (n.ip, n.membership_version, n.group_id, group["version"], self.source_revision)
                    for n in nodes
                }
                policy = QualityPolicy(**(policy_override or group["policy"]))

            async def check(node):
                try:
                    evidence = await self.evidence(node, policy)
                    return node, evidence, evaluate(policy, evidence)
                except Exception:
                    evidence = {"network": {"error": "检测任务失败，未提交替换"}}
                    return node, evidence, evaluate(policy, evidence)

            results = await asyncio.gather(*(check(n) for n in nodes))
            any_network_success = any(e.get("network", {}).get("successes", 0) for _, e, _ in results)
            output = []
            async with self.g.changed:
                self.g._writable()
                for node, evidence, verdict in results:
                    verdict = {
                        **verdict,
                        "evidence": evidence,
                        "checked_at": time.time(),
                        "expires_at": min(
                            [time.time() + policy.ttl_hours * 3600]
                            + [
                                v["checked_at"] + policy.ttl_hours * 3600
                                for v in evidence.values()
                                if isinstance(v, dict) and "checked_at" in v
                            ]
                        ),
                        "version": tokens[node.id][3],
                        "source_revision": tokens[node.id][4],
                        "node_id": node.id,
                        "ip": node.ip,
                    }
                    output.append(verdict)
                    valid = (
                        node.ip,
                        node.membership_version,
                        node.group_id,
                        self.groups.get(group_id, {}).get("version"),
                        self.source_revision,
                    ) == tokens[node.id]
                    if (
                        preview
                        or not valid
                        or node.used
                        or node.state not in {IPState.FRESH, IPState.QUARANTINED}
                    ):
                        continue
                    node.quality = verdict
                    net = evidence.get("network", {})
                    node.latency_ms = net.get("max_latency_ms")
                    node.checked_at = time.time()
                    if verdict["state"] == "PASS" and not self.g.collision(node):
                        node.state, node.reason = IPState.FRESH, "本组质量准入通过"
                        node.last_success_at = time.time()
                        self.slots[node.slot_id]["streak"] = 0
                        self.g.rotation.restore(node)
                    else:
                        node.state = IPState.QUARANTINED
                        node.reason = "；".join(verdict["reasons"]) or "冷却或重复资源"
                        if (
                            verdict["state"] == "FAIL"
                            and not node.binding_id
                            and group["auto_replace"]
                            and any_network_success
                            and allow_replace
                        ):
                            reason = self.budget_reason(node)
                            if reason:
                                node.reason += f"；{reason}"
                            else:
                                self.g._retire(node, "质量拒绝：" + node.reason, cause="quality")
                if not preview:
                    self.g.event(
                        "quality",
                        "分组质量检测完成",
                        group_id=group_id,
                        count=len(results),
                        summary=dict(Counter(x["state"] for x in output)),
                    )
                await self.g._persist()
                self.g.changed.notify_all()
            return {
                "items": output,
                "summary": dict(Counter(x["state"] for x in output)),
                "preview": preview,
                "correlated_outage": bool(results) and not any_network_success,
            }
        finally:
            self.running.discard(lock_key)
            if "nodes" in locals():
                self.busy.difference_update(n.id for n in nodes)

    def budget_reason(self, node, job=None, reservation_id=""):
        if node.group_id == "default":
            return None
        group, slot = self.groups.get(node.group_id), self.slots.get(node.slot_id)
        if not group or not slot:
            return "成员归属不明，暂停提交"
        if not group["enabled"]:
            return "分组已暂停"
        if job and job.cause == "quality" and not group["auto_replace"]:
            return "质量自动更换已关闭"
        day = self.day()
        history = [x for x in self.ledger if x["group_id"] == node.group_id]
        pending = self.g.rotation.pending(exclude=reservation_id)
        group_pending = [r for r in pending if r["group_id"] == node.group_id]
        if (
            sum(x["day"] == day for x in self.ledger) + sum(r["group_id"] != "default" for r in pending)
            >= self.global_daily_limit
        ):
            return "全局每日替换预算已用尽"
        if sum(x["day"] == day for x in history) + len(group_pending) >= group["daily_limit"]:
            return "分组每日替换预算已用尽"
        if (
            sum(x["round_id"] == group["round_id"] for x in history) + len(group_pending)
            >= group["round_limit"]
        ):
            return "本轮替换预算已用尽"
        if slot["streak"] >= group["slot_limit"]:
            return "该席位连续替换上限，需检查策略或资源来源"
        if time.time() - slot["last_submit"] < group["cooldown_seconds"]:
            return "替换间隔冷却中"
        return None

    def debit(self, node, job):
        if node.group_id == "default":
            return
        slot, group = self.slots[node.slot_id], self.groups[node.group_id]
        slot["streak"] += 1
        slot["last_submit"] = time.time()
        self.ledger.append(
            {
                "job_id": job.id,
                "group_id": node.group_id,
                "slot_id": node.slot_id,
                "day": self.day(),
                "round_id": group["round_id"],
                "at": time.time(),
                "account_id": node.account_id,
                "cause": job.cause,
            }
        )

    def inherit(self, old, new):
        self.ensure_node(old)
        temporary = new.slot_id
        new.group_id, new.slot_id = old.group_id, old.slot_id
        new.membership_version = old.membership_version + 1
        new.quality = {}
        self.slots[new.slot_id]["node_id"] = new.id
        if temporary and temporary != new.slot_id:
            self.slots.pop(temporary, None)

    async def due(self):
        # Revisit blocked jobs without dropping uncertainty or bypassing account ordering.
        async with self.g.changed:
            changed = False
            for job in self.g.replacements.values():
                if job.state == "BUDGET_BLOCKED" and job.group_id != "default":
                    node = self.g.nodes[job.node_id]
                    if not self.budget_reason(node, job):
                        job.state, job.error = "QUEUED", None
                        changed = True
            if changed:
                await self.g._persist()
        for group in list(self.groups.values()):
            if group["id"] == "default" or not group["enabled"] or group["id"] in self.running:
                continue
            ids = [
                n.id
                for n in self.g.nodes.values()
                if n.group_id == group["id"]
                and not n.used
                and n.state in {IPState.FRESH, IPState.QUARANTINED}
                and n.id not in self.busy
                and (not self.eligible(n))
                and time.time() - n.quality.get("checked_at", 0) >= 300
            ][:100]
            if ids:
                self.g.spawn(self.run(group["id"], ids), "quality-scheduled")
