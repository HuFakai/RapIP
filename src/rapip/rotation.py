"""Persistent opt-in rotation and bounded supplier replacements. Scheduler owns all writes."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import ipaddress
import json
import time
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .models import GatewayError, IPState, uid
from .provider import ProviderError
from .quotas import public_usage


class RotationPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = False
    interval_seconds: int = Field(default=1800, ge=60, le=604800)
    idle_pause_seconds: int = Field(default=600, ge=0, le=604800)
    daily_limit: int = Field(default=0, ge=0, le=100000)
    total_limit: int = Field(default=0, ge=0, le=100000)
    mode: Literal["graceful", "hard"] = "graceful"
    fault_failover: bool = False


class Selection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["original", "any", "country", "asn", "ip_range"] = "original"
    country_code: str = Field(default="", pattern=r"^([A-Z]{2})?$")
    asn_numbers: list[str] = Field(default_factory=list, max_length=20)
    ip_ranges: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("asn_numbers")
    @classmethod
    def asns(cls, values):
        if any(not v.isascii() or not v.isdigit() or not 0 < int(v) < 2**32 for v in values):
            raise ValueError("ASN 必须是有效数字")
        return list(dict.fromkeys(values))

    @field_validator("ip_ranges")
    @classmethod
    def ranges(cls, values):
        for value in values:
            if ipaddress.ip_network(value, strict=True).version != 4:
                raise ValueError("仅支持 IPv4 CIDR")
        return list(dict.fromkeys(values))

    def supplier(self, node):
        data = {"type": self.type, "count": 1}
        if self.type in {"original", "country"}:
            country = node.country if self.type == "original" else self.country_code
            if not country:
                raise GatewayError("INVALID_SELECTION", "请选择国家", 422)
            data.update(type="country", country_code=country)
        elif self.type == "asn":
            if not self.asn_numbers:
                raise GatewayError("INVALID_SELECTION", "请选择 ASN", 422)
            data["asn_numbers"] = self.asn_numbers
        elif self.type == "ip_range":
            if not self.ip_ranges:
                raise GatewayError("INVALID_SELECTION", "请选择 CIDR 网段", 422)
            data["ip_ranges"] = self.ip_ranges
        return data


class ManualPreview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    node_ids: list[str] = Field(min_length=1, max_length=100)
    selection: Selection = Field(default_factory=Selection)
    bound_action: Literal["reject", "wait", "replace"] = "reject"


class ManualCommit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    preview_id: str = Field(pattern=r"^[a-f0-9]{32}$")


class RotationManager:
    def __init__(self, gateway):
        self.g = gateway
        self.policies = {}
        self.ledger = {}
        self.previews = {}
        self.requests = {}

    def load(self, data):
        for name in ("policies", "ledger", "previews", "requests"):
            setattr(self, name, data.get(name, {}))

    def snapshot(self):
        return {k: getattr(self, k) for k in ("policies", "ledger", "previews", "requests")}

    def policy(self, group):
        return self.policies.get(group, {**RotationPolicy().model_dump(), "epoch": "initial"})

    def failover(self, group):
        return self.policy(group)["fault_failover"]

    def public(self, group):
        policy = self.policy(group)
        records = [
            r
            for r in self.ledger.values()
            if r["group_id"] == group and r["kind"] == "scheduled" and r["state"] != "CANCELLED"
        ]
        return {
            **policy,
            "today_used": sum(r["day"] == self.g.quality.day() for r in records),
            "total_used": sum(r["epoch"] == policy["epoch"] for r in records),
            "timezone": "Asia/Shanghai",
            "bindings": [
                self.binding_status(b)
                for b in self.g.bindings.values()
                if b.group_id == group and not b.released
            ],
        }

    async def save(self, group, policy, reset=False):
        async with self.g.changed:
            self.g._writable()
            if group not in self.g.quality.groups:
                raise GatewayError("NOT_FOUND", "分组不存在", 404)
            old = self.policy(group)
            if reset and any(
                r["group_id"] == group and r["state"] == "RESERVED" for r in self.ledger.values()
            ):
                raise GatewayError("ROTATION_BUSY", "仍有预留任务，不能重置授权")
            self.policies[group] = {**policy.model_dump(), "epoch": uid() if reset else old["epoch"]}
            for binding in self.g.bindings.values():
                if binding.group_id == group:
                    binding.rotation = {}  # saving never triggers an immediate replacement
                    if any(t["binding_id"] == binding.id for t in self.g.tunnels.values()):
                        self.connected(binding)
            self.g.event(
                "rotation_policy",
                "轮换策略已保存" + ("；已重新授权总次数" if reset else "；额度计数保留"),
                group_id=group,
            )
            await self.g._persist()
        return self.public(group)

    def connected(self, binding):
        policy = self.policy(binding.group_id)
        state = binding.rotation
        if not state.get("next_at") or state.get("status") == "IDLE_PAUSED":
            state.update(next_at=time.time() + policy["interval_seconds"], status="RUNNING", error="")

    def binding_status(self, binding):
        policy = self.policy(binding.group_id)
        state = dict(binding.rotation)
        status = state.get("status", "WAITING_CONNECTION")
        if not policy["enabled"]:
            status = "OFF"
        elif (
            not any(t["binding_id"] == binding.id for t in self.g.tunnels.values())
            and time.time() - binding.last_detached >= policy["idle_pause_seconds"]
        ):
            status = "IDLE_PAUSED"
        return {
            **state,
            "status": status,
            "binding_id": binding.id,
            "session": binding.session,
            "worker": binding.worker,
        }

    def pending(self, account=None, group=None, exclude=""):
        return [
            r
            for r in self.ledger.values()
            if r["state"] == "RESERVED"
            and r["id"] != exclude
            and (not account or r["account_id"] == account)
            and (not group or r["group_id"] == group)
        ]

    def budget(self, node, exclude=""):
        g = self.g
        account = g.accounts[node.account_id]
        if not account.enabled:
            raise GatewayError("BUDGET_BLOCKED", "账号已暂停，停止新的替换提交")
        reserved = len(self.pending(account=node.account_id, exclude=exclude))
        if (
            account.replacement_budget is not None
            and g.counters[node.account_id]["submitted"] + reserved >= account.replacement_budget
        ):
            raise GatewayError("BUDGET_BLOCKED", "账号本地预算不足（含已预留任务）")
        usage = public_usage(g.supplier_usage.get(node.account_id))
        remaining = usage.get("replacement", {}).get("remaining")
        if usage.get("stale") or remaining is None:
            raise GatewayError("USAGE_UNKNOWN", "供应商更换余额缺失或过期，请先同步套餐")
        since = usage.get("checked_at", 0)
        unsettled = sum(
            r["account_id"] == node.account_id
            and r["state"] == "SUBMITTED"
            and r.get("submitted_at", 0) >= since
            for r in self.ledger.values()
        )
        if remaining <= reserved + unsettled:
            raise GatewayError("BUDGET_BLOCKED", "供应商剩余额度不足（含本地预留及未反映提交）")
        reason = g.quality.budget_reason(node, reservation_id=exclude)
        if reason:
            raise GatewayError("BUDGET_BLOCKED", reason)

    def reserve(self, node, kind, binding_id=""):
        self.budget(node)
        if any(r["node_id"] == node.id for r in self.pending()):
            raise GatewayError("NODE_BUSY", "该出口已有预留替换任务")
        policy = self.policy(node.group_id)
        if kind == "scheduled":
            public = self.public(node.group_id)
            if public["today_used"] >= policy["daily_limit"] or public["total_used"] >= policy["total_limit"]:
                raise GatewayError("BUDGET_BLOCKED", "本组每日或本次启用自动轮换额度已用尽")
        rid = uid()
        self.ledger[rid] = {
            "id": rid,
            "node_id": node.id,
            "group_id": node.group_id,
            "account_id": node.account_id,
            "binding_id": binding_id,
            "kind": kind,
            "epoch": policy["epoch"],
            "day": self.g.quality.day(),
            "created_at": time.time(),
            "state": "RESERVED",
        }
        return rid

    def check_reservation(self, rid, binding=None):
        record = self.ledger[rid]
        if record["state"] != "RESERVED" or (
            binding
            and (
                record["binding_id"] != binding.id
                or record["node_id"] != binding.node_id
                or not self.policy(binding.group_id)["enabled"]
            )
        ):
            raise GatewayError("VERSION_CONFLICT", "轮换预留已变化或计划已关闭")
        self.budget(self.g.nodes[record["node_id"]], exclude=rid)

    def bind_reservation(self, rid, node):
        for job in self.g.replacements.values():
            if job.node_id == node.id and job.state == "QUEUED":
                job.reservation_id = rid
                break
        self.ledger[rid]["committed"] = True

    def reservation_for(self, node):
        return next((r["id"] for r in self.pending() if r["node_id"] == node.id and r.get("committed")), "")

    def submitted(self, job):
        if job.reservation_id:
            self.ledger[job.reservation_id].update(state="SUBMITTED", submitted_at=time.time(), job_id=job.id)

    def restore(self, node):
        # A supplier-replaced bound node is reserved until its quality evidence passes.
        binding = self.g.bindings.get(node.binding_id)
        if (
            binding
            and not binding.released
            and binding.node_id == node.id
            and node.state == IPState.FRESH
            and self.g.quality.eligible(node)
        ):
            node.state, node.used, node.generation = IPState.IN_USE, True, binding.generation
            binding.unavailable = ""
            node.reason = "显式更换后恢复长期绑定"

    async def tick(self):
        g = self.g
        # One rotation per tick provides staggering; a pending refill for the group blocks the next.
        for job in g.replacements.values():
            if job.state == "BUDGET_BLOCKED" and job.reservation_id:
                try:
                    self.check_reservation(job.reservation_id)
                    job.state, job.error = "QUEUED", None
                except GatewayError:
                    pass
        for binding in list(g.bindings.values()):
            policy = self.policy(binding.group_id)
            if (
                binding.released
                or not policy["enabled"]
                or not g.quality.groups.get(binding.group_id, {}).get("enabled")
            ):
                continue
            state = binding.rotation
            if self.binding_status(binding)["status"] == "IDLE_PAUSED":
                state.update(status="IDLE_PAUSED", next_at=None)
                continue
            if not state.get("next_at") or state["next_at"] > time.time() or binding.unavailable:
                continue
            if any(
                n.group_id == binding.group_id and n.state == IPState.DRAINING for n in g.nodes.values()
            ) or any(
                j.group_id == binding.group_id and j.state not in {"COMPLETED", "FAILED", "CANCELLED"}
                for j in g.replacements.values()
            ):
                state.update(status="REFILLING", error="等待本组补货/对账完成")
                continue
            rid = ""
            try:
                async with g.changed:
                    g._writable()
                    if not g._fresh(binding.country, binding.group_id):
                        raise GatewayError("POOL_EXHAUSTED", "没有符合要求的备用出口，原绑定保留")
                    node = g.nodes.get(binding.node_id)
                    if not node:
                        continue
                    rid = self.reserve(node, "scheduled", binding.id)
                    await g._persist()
                await g.rotate(
                    binding.id,
                    policy["mode"],
                    expected=binding.generation,
                    reservation_id=rid,
                    source="scheduled",
                )
                binding.rotation.update(next_at=time.time() + policy["interval_seconds"], status="RUNNING")
            except (GatewayError, TimeoutError) as error:
                state.update(
                    status="BUDGET_PAUSED"
                    if isinstance(error, GatewayError) and error.code in {"USAGE_UNKNOWN", "BUDGET_BLOCKED"}
                    else "WAITING_STOCK",
                    error=str(error),
                    next_at=time.time() + 30,
                )
                if rid and not self.ledger[rid].get("committed"):
                    self.ledger[rid]["state"] = "CANCELLED"
            break
        for request in list(self.requests.values()):
            if request["state"] != "WAITING":
                continue
            async with g.changed:
                node = g.nodes.get(request["node_id"])
                if not node or self.identity(node) != request["identity"]:
                    request.update(state="CANCELLED", error="资源或绑定已变化，请重新预检")
                    self.ledger[request["reservation_id"]]["state"] = "CANCELLED"
                elif (
                    not node.binding_id or not g._count_node(node.id) or request["bound_action"] == "replace"
                ):
                    try:
                        self.check_reservation(request["reservation_id"])
                        self.start_manual(request, node)
                    except GatewayError as error:
                        request.update(state="CANCELLED", error=error.message)
                        self.ledger[request["reservation_id"]]["state"] = "CANCELLED"
                await g._persist()

    def identity(self, node):
        binding = self.g.bindings.get(node.binding_id)
        value = [
            node.id,
            node.ip,
            node.group_id,
            node.membership_version,
            node.state,
            node.binding_id,
            binding.generation if binding else None,
            binding.released if binding else None,
            self.g.quality.groups[node.group_id]["version"],
            self.g.quality.source_revision,
        ]
        return hashlib.sha256(json.dumps(value).encode()).hexdigest()

    def validate_node(self, node, body):
        g = self.g
        if (
            not node
            or node.state not in {IPState.FRESH, IPState.QUARANTINED, IPState.IN_USE}
            or node.id in g.quality.busy
            or node.id in g.probing
        ):
            raise GatewayError("NODE_BUSY", "资源不存在、正在检测或替换，请稍后重试")
        binding = g.bindings.get(node.binding_id)
        if binding and not binding.released and body.bound_action == "reject":
            raise GatewayError("BOUND_NODE", "该 IP 已长期绑定，请选择等待断连或显式更换")
        target = body.selection.supplier(node)
        country = target.get("country_code")
        allowed = g.quality.groups[node.group_id]["policy"].get("countries", [])
        if allowed and (not country or country not in allowed):
            raise GatewayError("COUNTRY_CONFLICT", "更换条件必须明确匹配本组允许国家")
        if binding and not binding.released and binding.country and country != binding.country:
            raise GatewayError("COUNTRY_CONFLICT", "长期绑定的国家条件不可改变，请创建新的 session")
        if not g.accounts[node.account_id].enabled:
            raise GatewayError("ACCOUNT_DISABLED", "账号已暂停")
        if any(
            j.node_id == node.id and j.state not in {"COMPLETED", "FAILED", "CANCELLED"}
            for j in g.replacements.values()
        ):
            raise GatewayError("NODE_BUSY", "已有替换任务，请先对账")
        return target

    async def preview(self, body):
        if len(set(body.node_ids)) != len(body.node_ids):
            raise GatewayError("INVALID_SELECTION", "IP 不能重复", 422)
        async with self.g.changed:
            items = []
            for node_id in body.node_ids:
                node = self.g.nodes.get(node_id)
                selection = self.validate_node(node, body)
                self.budget(node)
                items.append(
                    {
                        "node_id": node.id,
                        "ip": node.ip,
                        "account_id": node.account_id,
                        "group_id": node.group_id,
                        "identity": self.identity(node),
                        "selection": selection,
                    }
                )
        for item in items:
            async with self.g.account_locks[item["account_id"]]:
                provider = self.g.providers[item["account_id"]]
                if not hasattr(provider, "replace_selected"):
                    raise GatewayError("UNSUPPORTED", "供应商不支持参数预检")
                try:
                    data = await provider.replace_selected(item["ip"], item["selection"], dry_run=True)
                    for _ in range(20):
                        if data.get("state") in {"validated", "completed", "failed"}:
                            break
                        await asyncio.sleep(0.5)
                        data = await provider.replacement(str(data["id"]))
                    if (
                        data.get("state") not in {"validated", "completed"}
                        or data.get("proxies_removed") != 1
                        or data.get("proxies_added") != 1
                    ):
                        raise GatewayError("PREVIEW_FAILED", "供应商预检未通过或更换数量不是 1:1，请调整参数")
                    item["supplier_preview_id"] = str(data["id"])
                except ProviderError as error:
                    raise GatewayError(
                        "PREVIEW_FAILED", f"供应商预检失败（HTTP {error.status}），请检查套餐、库存及参数"
                    ) from None
        pid = uid()
        async with self.g.changed:
            self.previews[pid] = {
                "id": pid,
                "items": items,
                "body": body.model_dump(),
                "expires_at": time.time() + 300,
                "committed": False,
            }
            await self.g._persist()
        return copy.deepcopy(self.previews[pid])

    async def commit(self, pid):
        async with self.g.changed:
            self.g._writable()
            preview = self.previews.get(pid)
            if not preview or preview["expires_at"] < time.time() or preview["committed"]:
                raise GatewayError("PREVIEW_EXPIRED", "预检已过期或已提交，请重新预检")
            body = ManualPreview(**preview["body"])
            for item in preview["items"]:
                node = self.g.nodes.get(item["node_id"])
                self.validate_node(node, body)
                if self.identity(node) != item["identity"]:
                    raise GatewayError("VERSION_CONFLICT", "资源状态已变化，请重新预检")
            created = []
            try:
                for item in preview["items"]:
                    node = self.g.nodes[item["node_id"]]
                    rid = self.reserve(node, "manual", node.binding_id or "")
                    created.append(rid)
            except GatewayError:
                for rid in created:
                    self.ledger[rid]["state"] = "CANCELLED"
                raise
            ids = []
            for item, rid in zip(preview["items"], created, strict=True):
                request_id = uid()
                self.requests[request_id] = {
                    **item,
                    "id": request_id,
                    "reservation_id": rid,
                    "state": "WAITING",
                    "bound_action": body.bound_action,
                    "created_at": time.time(),
                    "error": "",
                }
                ids.append(request_id)
            preview["committed"] = True
            self.g.event(
                "manual_replacement", "已确认手动更换；等待条件满足后提交", count=len(ids), request_ids=ids
            )
            await self.g._persist()
        return {
            "request_ids": ids,
            "status": "WAITING",
            "message": "已预留额度；可在替换任务中查看或取消未执行任务",
        }

    def start_manual(self, request, node):
        binding = self.g.bindings.get(node.binding_id)
        if binding and not binding.released and binding.node_id != node.id:
            raise GatewayError("VERSION_CONFLICT", "绑定出口已变化")
        self.g._retire(node, "管理员确认更换 IP", cause="manual_supplier")
        job = next(j for j in self.g.replacements.values() if j.node_id == node.id and j.state == "QUEUED")
        job.selection, job.reservation_id = request["selection"], request["reservation_id"]
        if binding and not binding.released:
            job.rebind_id = binding.id
            binding.unavailable = "管理员更换中；等待新 IP 质量准入"
            binding.rotation = {}
            self.g._close_node(node.id)
        self.ledger[job.reservation_id]["committed"] = True
        request.update(state="QUEUED", job_id=job.id)

    async def cancel(self, request_id):
        async with self.g.changed:
            self.g._writable()
            request = self.requests.get(request_id)
            if not request or request["state"] != "WAITING":
                raise GatewayError("NOT_CANCELLABLE", "只有尚未执行的等待任务可以取消")
            request["state"] = "CANCELLED"
            self.ledger[request["reservation_id"]]["state"] = "CANCELLED"
            await self.g._persist()
        return {"cancelled": True}

    def public_requests(self):
        return [
            {
                **r,
                "replacement": (
                    {
                        "state": self.g.replacements[r["job_id"]].state,
                        "error": self.g.replacements[r["job_id"]].error,
                        "new_ip": self.g.replacements[r["job_id"]].new_ip,
                    }
                    if r.get("job_id") in self.g.replacements
                    else None
                ),
            }
            for r in sorted(self.requests.values(), key=lambda r: r["created_at"], reverse=True)
        ][:100]
