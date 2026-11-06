"""One durable, bounded in-place replacement per existing worker."""

import asyncio
import time
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .models import GatewayError, IPState
from .replenishment import ReplenishmentRule


class DynamicPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["pool", "pool_then_in_place", "in_place"] = "pool"
    wait_seconds: int = Field(default=25, ge=5, le=90)
    cooldown_seconds: int = Field(default=60, ge=10, le=86400)


class DynamicSwitch:
    def __init__(self, gateway):
        self.g = gateway
        self.policies = {}
        self.pending = {}
        self.last_commit = {}

    def load(self, state):
        self.policies = state.get("policies", {})
        self.pending = state.get("pending", {})
        self.last_commit = state.get("last_commit", {})

    def snapshot(self):
        return dict(policies=self.policies, pending=self.pending, last_commit=self.last_commit)

    def policy(self, group):
        return self.policies.get(group, DynamicPolicy().model_dump())

    async def save(self, group, policy):
        async with self.g.changed:
            self.g._writable()
            if group not in self.g.quality.groups:
                raise GatewayError("NOT_FOUND", "分组不存在", 404)
            self.policies[group] = policy.model_dump()
            await self.g._persist()
        return self.policy(group)

    def public(self, binding):
        current = self.pending.get(binding.id)
        if not current:
            return None
        job = self.g.replacements[current["job_id"]]
        return {
            "status": "ADMISSION" if current.get("candidate_id") else job.state,
            "job_id": job.id,
            "started_at": job.created_at,
            "requested_session": current["session"],
            "error": current.get("error") or job.error or "",
        }

    def reject_control(self, binding):
        if binding.id in self.pending:
            raise GatewayError("IN_PLACE_BUSY", "原位替换未完成；请先查看追踪或处理隔离出口")

    def active_for_job(self, job):
        item = self.pending.get(job.rebind_id)
        return item if item and item["job_id"] == job.id else None

    def should_start(self, binding, route):
        mode = self.policy(route.group)["mode"]
        # A never-used reservation can move through the pool without a paid replacement.
        return bool(
            route.worker
            and not binding.reserved
            and (
                mode == "in_place"
                or (mode == "pool_then_in_place" and not self.g._fresh(route.country, route.group))
            )
        )

    def retire_session(self, binding, session, reason):
        key = f"{binding.slot}:{session}"
        self.g.retired[key] = time.time() + self.g.settings.retired_session_ttl
        self.g.retired_details[key] = dict(at=time.time(), reason=reason, binding_id=binding.id)

    def trace(self, kind, binding, item, node, **fields):
        self.g.lifecycle.add(
            kind,
            node=node,
            binding=binding,
            chain_id=item["chain_id"],
            session=item["session"],
            old_session=fields.pop("old_session", binding.session),
            generation=item["generation"] + 1,
            job_id=item["job_id"],
            **fields,
        )

    async def start_or_join(self, binding, route):
        g = self.g
        if not g.quality.groups[route.group]["enabled"]:
            raise GatewayError("GROUP_DISABLED", "分组已暂停新切换", 403)
        if item := self.pending.get(binding.id):
            if route.session in item["superseded"]:
                raise GatewayError("SESSION_RETIRED", "本 session 已被更新的候选替代")
            if route.session == binding.session:
                raise GatewayError("IN_PLACE_PENDING", "工位正在原位替换；请用本次申请的新 session 重试", 503)
            if route.country != item["country"]:
                raise GatewayError("COUNTRY_CONFLICT", "在途原位替换的国家条件不可更改")
            if route.session != item["session"]:
                # A delayed retry cannot replace a newer candidate.
                previous = item["session"]
                item["superseded"].append(previous)
                self.retire_session(binding, previous, "被原位任务中更新的 session 替代")
                item["session"] = route.session
                self.trace(
                    "in_place_join", binding, item, g.nodes.get(binding.node_id), reason="合并到在途任务"
                )
                await g._persist()
                g.changed.notify_all()
            return item["job_id"]

        node = g.nodes.get(binding.node_id)
        if not node or node.state != IPState.IN_USE or node.binding_id != binding.id:
            raise GatewayError("EXIT_UNAVAILABLE", "没有可原位替换的已使用出口，请核对当前绑定")
        if any(n.binding_id == binding.id and n.state == IPState.DRAINING for n in g.nodes.values()):
            raise GatewayError("DRAIN_BUSY", "旧代仍在排空，请稍后重试")
        if time.time() - self.last_commit.get(binding.id, 0) < self.policy(route.group)["cooldown_seconds"]:
            raise GatewayError(
                "IN_PLACE_COOLDOWN", "工位原位替换仍在冷却，请保留当前 session 并稍后重试", 503
            )
        if any(
            j.account_id == node.account_id
            and j.state in {"UNCERTAIN", "SUBMITTING", "PROCESSING", "RECONCILING"}
            for j in g.replacements.values()
        ):
            raise GatewayError("IN_PLACE_BUSY", "账号仍有在途或待核对的供应商任务", 503)

        rule = ReplenishmentRule.model_validate(g.replenishment.get(route.group, {}))
        selection = rule.selection(node.country)
        # A client country is a hard constraint. A different configured country is an
        # explicit conflict; ASN/CIDR cannot be combined with country in one supplier rule.
        if route.country:
            if selection["type"] == "country" and selection["country_code"] != route.country:
                raise GatewayError("COUNTRY_CONFLICT", "客户端指定国家与分组补货国家不一致")
            if selection["type"] in {"asn", "ip_range"}:
                raise GatewayError("COUNTRY_CONFLICT", "指定国家的客户端不能同时使用 ASN/网段原位替换")
            selection = {"type": "country", "country_code": route.country, "count": 1}
        if selection["type"] == "country" and not selection.get("country_code"):
            raise GatewayError("COUNTRY_CONFLICT", "原 IP 国家未知，无法按原国家更换")
        if g.waiters >= g.settings.max_waiters:
            raise GatewayError("QUEUE_FULL", "等待队列已满，请稍后重试", 503)
        rid = g.rotation.reserve(node, "in_place", binding.id)
        g._retire(node, "客户端申请原位替换", cause="client_in_place")
        job = next(j for j in g.replacements.values() if j.node_id == node.id and j.state == "QUEUED")
        chain = f"in-place:{job.id}"
        g.lifecycle.related[node.id] = chain
        job.rebind_id, job.reservation_id = binding.id, rid
        job.selection, job.preferred_selection = selection, selection
        # Do not fall back to a random country on a country-constrained client.
        job.fallback_enabled = rule.fallback and not route.country
        g.rotation.ledger[rid]["committed"] = True
        item = self.pending[binding.id] = {
            "job_id": job.id,
            "session": route.session,
            "country": route.country,
            "generation": binding.generation,
            "superseded": [],
            "chain_id": chain,
        }
        binding.unavailable = "原位替换中；新连接等待供应商与质量准入"
        binding.rotation = {}
        self.trace("in_place_started", binding, item, node, status="QUEUED")
        await g._persist()
        return job.id

    async def wait(self, route, binding_id, job_id):
        g = self.g
        async with g.changed:
            if g.waiters >= g.settings.max_waiters:
                raise GatewayError("QUEUE_FULL", "等待队列已满，请使用相同 session 退避重试", 503)
            g.waiters += 1
            try:
                async with asyncio.timeout(self.policy(route.group)["wait_seconds"]):
                    while True:
                        g._writable()
                        binding = g.bindings[binding_id]
                        item = self.pending.get(binding_id)
                        job = g.replacements[job_id]
                        if not item and job.state == "FAILED":
                            raise GatewayError("IN_PLACE_FAILED", job.error or "原位替换未执行", 503)
                        if g.retired.get(g._retired_key(route), 0) > time.time() or (
                            item and route.session in item["superseded"]
                        ):
                            raise GatewayError("SESSION_RETIRED", "本 session 已被更新的候选替代")
                        if not item or item["job_id"] != job_id:
                            node = g.nodes.get(binding.node_id)
                            if (
                                not binding.released
                                and binding.session == route.session
                                and node
                                and node.state == IPState.IN_USE
                            ):
                                binding.last_active = time.time()
                                return binding, node, binding.generation
                            raise GatewayError(
                                "IN_PLACE_FAILED", job.error or "原位任务未给当前 session 分配出口", 503
                            )
                        if job.state == "UNCERTAIN" or item.get("error"):
                            raise GatewayError("IN_PLACE_REVIEW", item.get("error") or job.error, 503)
                        await g.changed.wait()
            except TimeoutError:
                raise GatewayError(
                    "IN_PLACE_PENDING", "原位任务继续执行；请用同一个 session 退避重试", 503
                ) from None
            finally:
                g.waiters -= 1

    def fail(self, job, safe):
        g = self.g
        item = self.active_for_job(job)
        if not item:
            return
        binding, node = g.bindings[job.rebind_id], g.nodes[job.node_id]
        self.trace("in_place_failed", binding, item, node, status=job.state, reason=job.error)
        g.changed.notify_all()
        if not safe:
            item["error"] = job.error or "供应商结果不明"
            return
        if g.rotation.ledger[job.reservation_id]["state"] == "RESERVED":
            g.rotation.ledger[job.reservation_id]["state"] = "CANCELLED"
        self.retire_session(binding, item["session"], "原位替换未执行，原绑定保留")
        node.state, node.reason = IPState.IN_USE, "原位替换未执行，原绑定保留"
        g.blacklist.pop(node.ip, None)
        binding.unavailable = ""
        if job.attempts:
            self.last_commit[binding.id] = time.time()
        del self.pending[binding.id]

    def candidate(self, job, fresh, supplier_valid):
        item = self.active_for_job(job)
        if not item:
            return
        binding = self.g.bindings[job.rebind_id]
        item.update(candidate_id=fresh.id, supplier_valid=supplier_valid)
        fresh.state = IPState.QUARANTINED
        binding.node_id = fresh.id
        binding.unavailable = "Webshare 已更换；等待新 IP 质量准入"
        fresh.binding_id = binding.id
        self.trace("in_place_admission", binding, item, fresh, status="CHECKING")

    def before_release(self, binding):
        item = self.pending.get(binding.id)
        if not item:
            return
        if not item.get("candidate_id") or not item.get("error"):
            self.reject_control(binding)
        node = self.g.nodes[item["candidate_id"]]
        self.retire_session(binding, item["session"], "管理员释放未准入的原位任务")
        self.trace(
            "in_place_failed", binding, item, node, status="CANCELLED", reason="管理员释放；隔离 IP 保留"
        )
        node.binding_id = None
        del self.pending[binding.id]

    def admission_error(self, node, reason):
        binding = self.g.bindings.get(node.binding_id)
        item = self.pending.get(node.binding_id)
        if item and item.get("candidate_id") == node.id and item.get("error") != reason:
            item["error"] = reason
            self.trace("in_place_admission", binding, item, node, status="QUARANTINED", reason=reason)

    def restore(self, binding, node):
        """Called under changed after a genuine probe/admission result."""
        g = self.g
        item = self.pending.get(binding.id)
        if not item or item.get("candidate_id") != node.id:
            return False
        job = g.replacements[item["job_id"]]
        duplicate = any(
            n.id != node.id and n.ip == node.ip and n.state != IPState.ARCHIVED for n in g.nodes.values()
        )
        if (
            node.state != IPState.FRESH
            or not g.quality.eligible(node)
            or not item["supplier_valid"]
            or duplicate
            or (item["country"] and item["country"] != node.country)
        ):
            node.state = IPState.QUARANTINED
            if not item["supplier_valid"]:
                node.reason = "供应商标记新 IP 不可用"
            elif duplicate:
                node.reason = "新 IP 与已有资源重复"
            elif item["country"] and item["country"] != node.country:
                node.reason = "Webshare 返回的 IP 国家与客户端要求不一致"
            elif node.reason == "待分配":
                node.reason = "分组未启用，等待质量准入"
            self.admission_error(node, node.reason)
            return True
        self.trace("in_place_admission", binding, item, node, status="PASS")
        previous = binding.session
        self.retire_session(binding, previous, f"session 已被 {item['session']} 替代")
        for session in item["superseded"]:
            self.retire_session(binding, session, "被原位任务中更新的 session 替代")
        binding.session, binding.country = item["session"], item["country"]
        binding.generation = item["generation"] + 1
        g.lifecycle.keys[f"{binding.id}:{binding.generation}"] = item["chain_id"]
        binding.policy_version = g.quality.groups[binding.group_id]["version"]
        binding.unavailable, binding.last_active = "", time.time()
        node.state, node.used, node.generation = IPState.IN_USE, True, binding.generation
        node.reason = "原位替换完成，会话独占"
        self.trace(
            "switched",
            binding,
            item,
            node,
            old_session=previous,
            old_ip=g.nodes[job.node_id].ip,
            source="client_in_place",
            status="COMPLETED",
        )
        self.last_commit[binding.id] = time.time()
        del self.pending[binding.id]
        return True
