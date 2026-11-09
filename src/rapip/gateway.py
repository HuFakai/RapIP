from __future__ import annotations

import asyncio
import copy
import fcntl
import json
import logging
import time
from collections import Counter
from dataclasses import asdict

import aiohttp

from .config import Config, private_json
from .credentials import Credentials
from .dynamic import DynamicSwitch
from .ipquality import IPQualityReports
from .lifecycle import Lifecycle
from .models import Binding, GatewayError, IPState, Node, Operation, Replacement, Route, uid
from .observability import Observability
from .ping0 import Ping0Reports
from .provider import ProviderError, Webshare
from .quality import QualityManager
from .quotas import public_usage
from .replenishment import NO_STOCK, RANDOM, ReplenishmentRule, preflight
from .retention import RetentionPolicy
from .rotation import RotationManager
from .socks import probe_node
from .storage import Storage
from .workers import Workers

log = logging.getLogger(__name__)


class Gateway:
    def __init__(self, config: Config, provider_factory=Webshare, probe=probe_node):
        self.config, self.settings = config, config.settings
        self.credentials = Credentials(config)
        self.provider_factory, self.probe = provider_factory, probe
        if probe is probe_node and config.egress_proxy:

            async def configured_probe(node, timeout):
                return await probe_node(node, timeout, config.egress_proxy)

            self.probe = configured_probe
        self.storage = Storage(config.data_dir)
        self.lifecycle = Lifecycle(self)
        self.replenishment = {}
        self.returning = {}
        self.lock = asyncio.Lock()
        self.changed = asyncio.Condition(self.lock)
        self.account_locks: dict[str, asyncio.Lock] = {}
        self.slot_locks: dict[str, asyncio.Lock] = {}
        self.nodes: dict[str, Node] = {}
        self.bindings: dict[str, Binding] = {}
        self.replacements: dict[str, Replacement] = {}
        self.operations: dict[str, Operation] = {}
        self.idempotency: dict[str, dict] = {}
        self.removed_accounts: dict[str, dict] = {}
        self.blacklist: dict[str, float] = {}
        self.retired: dict[str, float] = {}
        self.retired_details: dict[str, dict] = {}
        self.dialing: dict[str, int] = {}
        self.events: list[dict] = []
        self.counters: dict[str, dict] = {}
        self.account_health: dict[str, dict] = {}
        self.supplier_usage: dict[str, dict] = {}
        self.usage_locks: dict[str, asyncio.Lock] = {}
        self.tunnels: dict[str, dict] = {}
        self.tasks: set[asyncio.Task] = set()
        self.probing: set[str] = set()
        self.waiters = 0
        self.storage_failed = False
        self.stopping = False
        self.ready = False
        self.started_at = time.time()
        self.bytes_up = self.bytes_down = 0
        self.observer = Observability(config.data_dir)
        self.ipquality = IPQualityReports(self)
        self.ping0 = Ping0Reports(self)
        self.quality = QualityManager(self)
        self.rotation = RotationManager(self)
        self.dynamic = DynamicSwitch(self)
        self.workers = Workers(self)
        self.report_methods = {"ipquality": True, "ping0": False}

    async def start(self, background: bool = True, sync: bool = True):
        self.config.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.owner_file = open(self.config.data_dir / "gateway.lock", "a+")
        try:
            fcntl.flock(self.owner_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.owner_file.close()
            raise RuntimeError("This data directory already has a running gateway; use one worker") from None
        await self.storage.open()
        state = await self.storage.load()
        for name, model in (
            ("nodes", Node),
            ("bindings", Binding),
            ("replacements", Replacement),
            ("operations", Operation),
        ):
            setattr(self, name, {key: model(**value) for key, value in state.get(name, {}).items()})
        for name in (
            "blacklist",
            "retired",
            "events",
            "counters",
            "idempotency",
            "removed_accounts",
            "supplier_usage",
            "retired_details",
        ):
            if name in state:
                setattr(self, name, state[name])
        for key, value in state.get("settings", {}).items():
            if key == "probe_enabled":
                self.config.probe_enabled = value
            if hasattr(self.settings, key):
                setattr(self.settings, key, value)
        self.bytes_up, self.bytes_down = state.get("bytes_up", 0), state.get("bytes_down", 0)
        self.observer.mode = state.get("log_mode", "errors")
        self.observer.retention = RetentionPolicy.model_validate(state.get("retention", {}))
        self.quality.load(state.get("quality", {}))
        self.rotation.load(state.get("rotation", {}))
        self.dynamic.load(state.get("dynamic", {}))
        self.workers.load(state.get("workers", {}))
        self.replenishment = state.get("replenishment", {})
        self.returning = state.get("returning", {})
        self.lifecycle.related = state.get("lifecycle_related", {})
        self.lifecycle.keys = state.get("lifecycle_keys", {})
        self.lifecycle.load(state.get("lifecycle_initialized", False))
        self.report_methods["ipquality"] = state.get("report_methods", {}).get("ipquality", True)
        self.report_methods["ping0"] = state.get("report_methods", {}).get("ping0", False)
        self.settings.auto_failover = False  # v0.2.5: opt in per group, never inherit legacy global True
        await self.observer.open()
        self.client = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=25, connect=8),
            connector=aiohttp.TCPConnector(limit=20, limit_per_host=10),
        )
        if self.removed_accounts:
            try:
                await asyncio.to_thread(self._cleanup_removed_account_files)
            except OSError:
                log.warning("Removed account files require cleanup; deleted accounts remain disabled")
        self.reload_accounts()
        async with self.changed:
            for job in self.replacements.values():
                if (
                    state.get("schema", 2) < 3
                    and job.state == "QUEUED"
                    and not job.attempts
                    and job.reason in {"绑定释放", "进程恢复：旧连接已结束"}
                ):
                    job.state, job.error = (
                        "FAILED",
                        "旧版本回收任务来源不明，已暂停；请在资源池人工核对后决定更换",
                    )
                    if node := self.nodes.get(job.node_id):
                        node.state, node.reason = IPState.QUARANTINED, job.error
                if job.state == "SUBMITTING":
                    job.state, job.error = "UNCERTAIN", "提交结果不明；需对账，禁止自动重复提交"
            self._recover_failed_replacements()
            for node in self.nodes.values():
                if node.state == IPState.DRAINING:
                    if node.id in self.returning:
                        self._return_node(node)
                    else:
                        self._retire(node, "重启后旧代次已排空")
            for binding in self.bindings.values():
                if not binding.released:
                    binding.last_detached = time.time()
                    binding.rotation = {}  # resume a full interval only after real successful connection
                    node = self.nodes.get(binding.node_id)
                    if (
                        node
                        and node.state in {IPState.IN_USE, IPState.RESERVED}
                        and node.binding_id == binding.id
                    ):
                        binding.unavailable = ""
                    else:
                        binding.unavailable = "原出口不可用，等待核对或显式更换"
            for op in self.operations.values():
                if op.state not in {"COMPLETED", "FAILED"}:
                    op.state, op.error = "FAILED", "进程重启；请查看绑定现状，未自动重放操作"
            await self._persist()
        if sync:
            await self.sync_all()
        self.ready = True
        if background:
            self.spawn(self._periodic(), "inspector")
            self.spawn(self._replacement_loop(), "replacement")
            self.spawn(self._observe(), "observations")
        return self

    def reload_accounts(self):
        accounts = [a for a in self.config.accounts() if a.account_id not in self.removed_accounts]
        if hasattr(self, "accounts"):
            existing = {a.account_id for a in accounts}
            if not (set(self.accounts) - set(self.removed_accounts)).issubset(existing):
                raise GatewayError("UNSUPPORTED_RELOAD", "首版不支持热删除账号", 400)
            for account in accounts:
                previous = self.accounts.get(account.account_id)
                if previous and previous.plan_id != account.plan_id:
                    raise GatewayError("UNSUPPORTED_RELOAD", "首版不支持热切换套餐", 400)
        self.accounts = {a.account_id: a for a in accounts}
        self.providers = {
            a.account_id: self.provider_factory(a, self.config.key(a), self.client) for a in accounts
        }
        for account in accounts:
            self.account_locks.setdefault(account.account_id, asyncio.Lock())
            self.counters.setdefault(account.account_id, {"submitted": 0, "completed": 0, "redraws": 0})

    def spawn(self, coroutine, name: str = "operation"):
        task = asyncio.create_task(coroutine, name=f"rapip:{name}")
        self.tasks.add(task)

        def done(finished):
            self.tasks.discard(finished)
            if not finished.cancelled() and finished.exception():
                log.error("Background task %s failed: %s", name, type(finished.exception()).__name__)
                if name in {"inspector", "replacement"}:
                    self.ready = False

        task.add_done_callback(done)
        return task

    async def stop(self):
        self.stopping = True
        for tunnel in list(self.tunnels.values()):
            tunnel["client"].close()
            tunnel["upstream"].close()
        for task in list(self.tasks):
            task.cancel()
        if self.tasks:
            await asyncio.gather(*self.tasks, return_exceptions=True)
        if not self.storage_failed:
            async with self.lock:
                await self._persist()
        await self.observer.flush(self)
        await self.observer.close()
        await self.client.close()
        await self.storage.close()
        self.owner_file.close()

    def _snapshot(self):
        for group in self.quality.groups:
            self.workers.remember_capacity(group)
        return {
            "schema": 6,
            "lifecycle_related": self.lifecycle.related,
            "lifecycle_keys": self.lifecycle.keys,
            "lifecycle_initialized": self.lifecycle.initialized,
            "replenishment": self.replenishment,
            "returning": self.returning,
            "workers": self.workers.snapshot(),
            "report_methods": self.report_methods,
            "rotation": self.rotation.snapshot(),
            "dynamic": self.dynamic.snapshot(),
            "quality": self.quality.snapshot(),
            "bytes_up": self.bytes_up,
            "bytes_down": self.bytes_down,
            "log_mode": self.observer.mode,
            "retention": self.observer.retention.model_dump(),
            **{
                name: {key: asdict(value) for key, value in getattr(self, name).items()}
                for name in ("nodes", "bindings", "replacements", "operations")
            },
            **{
                name: getattr(self, name)
                for name in (
                    "blacklist",
                    "retired",
                    "events",
                    "counters",
                    "idempotency",
                    "removed_accounts",
                    "supplier_usage",
                    "retired_details",
                )
            },
            "settings": {**self.settings.public(), "probe_enabled": self.config.probe_enabled},
        }

    def _recover_failed_replacements(self):
        """A terminal job cannot keep an orphan resource marked as actively replacing."""
        jobs_by_node = {}
        for job in self.replacements.values():
            jobs_by_node.setdefault(job.node_id, []).append(job)
        for node in self.nodes.values():
            if node.state != IPState.REPLACING:
                continue
            jobs = jobs_by_node.get(node.id, [])
            if not jobs or any(j.state not in {"FAILED", "COMPLETED", "CANCELLED"} for j in jobs):
                continue
            job = max(jobs, key=lambda j: j.created_at)
            if job.state != "FAILED" or self.dynamic.active_for_job(job):
                continue
            if not job.attempts and not job.provider_id:
                reservation = self.rotation.ledger.get(job.reservation_id)
                if reservation and reservation["state"] == "RESERVED":
                    reservation["state"] = "CANCELLED"
            binding = self.bindings.get(node.binding_id)
            bound = binding and not binding.released and binding.node_id == node.id
            if not job.attempts and not job.provider_id and bound:
                node.state = IPState.RESERVED if binding.reserved else IPState.IN_USE
                node.replacement_error = ""
                binding.unavailable = ""
                node.reason = "替换未提交，原 IP 与绑定保留"
                self.blacklist.pop(node.ip, None)
            else:
                node.state = IPState.QUARANTINED
                node.replacement_error = job.error or "替换任务失败，请核对后手动重试"
                node.reason = "补货失败，旧 IP 保留；" + node.replacement_error
                if not bound:
                    node.binding_id = None
            self.lifecycle.add(
                "replacement_blocked",
                node=node,
                job_id=job.id,
                reason=job.error,
                status=node.state,
                attempt=job.attempts,
                chain_id=self.lifecycle.related.get(node.id),
            )

    async def _persist(self):
        if self.storage_failed:
            raise GatewayError("STORAGE_UNAVAILABLE", "持久化不可用，已停止新的状态变更", 503)
        self._recover_failed_replacements()
        self.lifecycle.collect_jobs()
        rows, self.lifecycle.pending = self.lifecycle.pending, []
        cutoff, self.lifecycle.cutoff = self.lifecycle.cutoff, None
        task = asyncio.create_task(
            self.storage.save(self._snapshot(), rows, cutoff, self.observer.retention.max_trace_records)
        )
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            try:
                await task
            except Exception:
                self.storage_failed = True
            raise
        except Exception:
            self.storage_failed = True
            self.ready = False
            raise GatewayError("STORAGE_UNAVAILABLE", "持久化失败，新的状态变更已暂停", 503) from None

    def _writable(self):
        if self.stopping or self.storage_failed:
            raise GatewayError("UNAVAILABLE", "网关暂不接受新的状态变更", 503)

    def event(self, kind: str, message: str, **detail):
        self.events.append({"id": uid(), "kind": kind, "message": message, "at": time.time(), **detail})
        self.events = self.events[-1000:]

    def binding_for(self, slot: str) -> Binding | None:
        return next((b for b in self.bindings.values() if b.slot == slot and not b.released), None)

    def active_limit(self) -> int:
        total = sum(
            n.state != IPState.ARCHIVED
            and self.accounts.get(n.account_id) is not None
            and self.accounts[n.account_id].enabled
            for n in self.nodes.values()
        )
        return self.settings.max_active_bindings or max(0, total - self.settings.min_spare_ips)

    def _fresh(self, country: str | None, group_id="default") -> Node | None:
        now = time.time()
        candidates = [
            n
            for n in self.nodes.values()
            if self._fresh_eligible(n)
            and n.group_id == group_id
            and not n.binding_id
            and self.accounts[n.account_id].enabled
            and self.account_health.get(n.account_id, {}).get("state") == "healthy"
            and (not country or n.country == country)
            and self.blacklist.get(n.ip, 0) <= now
        ]
        return min(candidates, key=lambda n: n.created_at) if candidates else None

    def _retired_key(self, route: Route) -> str:
        return f"{route.slot}:{route.session}"

    async def acquire(self, route: Route):
        slot_lock = self.slot_locks.setdefault(route.slot, asyncio.Lock())
        result = await self._acquire(route, slot_lock)
        if len(result) == 2:
            return await self.dynamic.wait(route, *result)
        return result

    async def _acquire(self, route, slot_lock):
        async with asyncio.timeout(self.settings.pool_wait_timeout + 1):
            async with slot_lock:
                async with self.changed:
                    self._writable()
                    if route.group not in self.quality.groups:
                        raise GatewayError("GROUP_DISABLED", "分组不存在或已暂停", 403)
                    if self.retired.get(self._retired_key(route), 0) > time.time():
                        detail = self.retired_details.get(self._retired_key(route), {})
                        raise GatewayError(
                            "SESSION_RETIRED",
                            "session 已失效："
                            + detail.get("reason", "旧版本记录，原因未完整保留")
                            + "；请使用新的 session",
                        )
                    binding = self.binding_for(route.slot)
                    self.workers.validate(route, binding)
                    self.workers.remember_capacity(route.group)
                    if binding and (
                        binding.id in self.dynamic.pending
                        or (binding.session != route.session and self.dynamic.should_start(binding, route))
                    ):
                        job_id = await self.dynamic.start_or_join(binding, route)
                        return binding.id, job_id
                    if binding and binding.session == route.session:
                        if binding.country != route.country:
                            raise GatewayError("COUNTRY_CONFLICT", "同一 session 不能修改国家条件")
                        node = self.nodes.get(binding.node_id)
                        if node and node.state == IPState.RESERVED:
                            node.state, node.used, binding.reserved = IPState.IN_USE, True, False
                            self.workers.consume(route)
                            node.reason = "预留链接已启用，会话独占"
                            self.lifecycle.add("assigned", node=node, binding=binding, reason=node.reason)
                            await self._persist()
                        if node and node.state == IPState.IN_USE:
                            binding.last_active = time.time()
                            return binding, node, binding.generation
                        if binding.unavailable or not self.rotation.failover(binding.group_id):
                            raise GatewayError(
                                "EXIT_UNAVAILABLE", binding.unavailable or "原出口不可用，请显式更换"
                            )
                        return await self._allocate(binding, route)
                    if binding:
                        await self._switch(
                            binding,
                            route.session,
                            self.settings.session_switch_mode,
                            route.country,
                            source="client_session",
                        )
                        return binding, self.nodes[binding.node_id], binding.generation
                    return await self._allocate(None, route)

    async def _wait_node(self, country, new_binding: bool, group_id="default"):
        deadline = asyncio.get_running_loop().time() + self.settings.pool_wait_timeout
        if self.waiters >= self.settings.max_waiters:
            raise GatewayError("QUEUE_FULL", "分配等待队列已满", 503)
        self.waiters += 1
        try:
            while True:
                self._writable()
                if not self.quality.groups.get(group_id, {}).get("enabled"):
                    raise GatewayError("GROUP_DISABLED", "分组已暂停新分配", 403)
                active = sum(not b.released for b in self.bindings.values())
                spare = sum(self._fresh_eligible(n) for n in self.nodes.values())
                if (
                    not new_binding or (active < self.active_limit() and spare > self.settings.min_spare_ips)
                ) and (node := self._fresh(country, group_id)):
                    return node
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    code = (
                        "BINDING_LIMIT_REACHED"
                        if new_binding and active >= self.active_limit()
                        else "POOL_EXHAUSTED"
                    )
                    raise GatewayError(code, "可用资源不足，等待超时；原绑定保持不变", 503)
                try:
                    await asyncio.wait_for(self.changed.wait(), remaining)
                except TimeoutError:
                    raise GatewayError("POOL_EXHAUSTED", "没有符合条件的热备 IP", 503) from None
        finally:
            self.waiters -= 1

    async def _allocate(self, binding: Binding | None, route: Route):
        node = await self._wait_node(route.country, binding is None, route.group)
        if binding and binding.released:
            raise GatewayError("BINDING_RELEASED", "等待期间绑定已释放，请创建新 session")
        if not binding:
            binding = Binding(
                uid(),
                route.slot,
                route.session,
                route.worker,
                route.country,
                node.id,
                group_id=route.group,
                policy_version=self.quality.groups[route.group]["version"],
            )
            self.bindings[binding.id] = binding
        else:
            binding.node_id, binding.last_active = node.id, time.time()
        binding.unavailable = ""
        binding.reserved = False
        node.state, node.used, node.binding_id, node.generation = (
            IPState.IN_USE,
            True,
            binding.id,
            binding.generation,
        )
        node.reason = "会话独占"
        self.workers.consume(route)
        self.lifecycle.add("assigned", node=node, binding=binding)
        self.event(
            "binding",
            "分配出口",
            binding_id=binding.id,
            ip=node.ip,
            group_id=node.group_id,
            account_id=node.account_id,
        )
        await self._persist()
        return binding, node, binding.generation

    def _close_node(self, node_id: str):
        for tunnel in list(self.tunnels.values()):
            if tunnel["node_id"] == node_id:
                tunnel["client"].close()
                tunnel["upstream"].close()

    def _count_node(self, node_id):
        return sum(t["node_id"] == node_id for t in self.tunnels.values())

    def _retire(self, node: Node, reason: str, replace: bool = True, redraws: int = 0, cause="business"):
        if node.state in {IPState.ARCHIVED, IPState.REPLACING}:
            return
        node.reason, node.drain_until = reason, None
        self.blacklist[node.ip] = max(
            self.blacklist.get(node.ip, 0), time.time() + self.settings.blacklist_ttl
        )
        if replace:
            node.replacement_error = ""
            node.state = IPState.REPLACING
            self.quality.ensure_node(node)
            job = Replacement(
                uid(),
                node.id,
                node.account_id,
                reason=reason,
                redraws=redraws,
                group_id=node.group_id,
                slot_id=node.slot_id,
                cause="collision" if redraws else cause,
            )
            if cause != "manual_supplier":
                rule = ReplenishmentRule.model_validate(self.replenishment.get(node.group_id, {}))
                job.selection = rule.selection(node.country)
                job.preferred_selection = job.selection
                job.fallback_enabled = rule.fallback
            job.reservation_id = self.rotation.reservation_for(node)
            self.replacements[job.id] = job
        else:
            node.state = IPState.ARCHIVED

    async def _switch(
        self,
        binding: Binding,
        session: str,
        mode: str,
        country: str | None,
        reservation_id="",
        source="manual",
    ):
        self.dynamic.reject_control(binding)
        generation_before_wait = binding.generation
        draining = [
            n for n in self.nodes.values() if n.binding_id == binding.id and n.state == IPState.DRAINING
        ]
        if draining and mode == "graceful":
            raise GatewayError("DRAIN_BUSY", "上一代连接仍在排空，请等待或选择强制切换")
        if session != binding.session and self.retired.get(f"{binding.slot}:{session}", 0) > time.time():
            raise GatewayError("SESSION_RETIRED", "新 session 已失效，请生成其他 ID")
        requested = uid()
        request_chain = f"request:{requested}"
        old_at_request = self.nodes.get(binding.node_id)
        self.lifecycle.add(
            "session_requested" if session != binding.session else "rotation_requested",
            node=old_at_request,
            binding=binding,
            old_session=binding.session,
            session=session,
            old_ip=old_at_request.ip if old_at_request else None,
            generation=binding.generation + 1,
            chain_id=request_chain,
            source=source,
            status="WAITING",
            country=old_at_request.country if old_at_request else "",
        )
        await self._persist()
        try:
            node = await self._wait_node(country, False, binding.group_id)
        except (GatewayError, TimeoutError) as error:
            self.lifecycle.add(
                "request_failed",
                node=old_at_request,
                binding=binding,
                session=session,
                chain_id=request_chain,
                reason=str(error),
                source=source,
                status="FAILED",
            )
            await self._persist()
            raise
        self.dynamic.reject_control(binding)
        if binding.released or binding.generation != generation_before_wait:
            raise GatewayError("VERSION_CONFLICT", "等待期间绑定已变化，请重试")
        if reservation_id:
            self.rotation.check_reservation(reservation_id, binding)
        old = self.nodes.get(binding.node_id)
        # No await between reservation and commit: concurrent sessions cannot consume
        # the spare pool without funding the old exit's refill first.
        if old and old.used and not reservation_id:
            superseded = [
                r
                for r in self.rotation.requests.values()
                if r["node_id"] == old.id and r["state"] == "WAITING"
            ]
            for request in superseded:
                self.rotation.ledger[request["reservation_id"]]["state"] = "CANCELLED"
            try:
                reservation_id = self.rotation.reserve(old, source, binding.id)
            except GatewayError as error:
                for request in superseded:
                    self.rotation.ledger[request["reservation_id"]]["state"] = "RESERVED"
                self.lifecycle.add(
                    "request_failed",
                    node=old,
                    binding=binding,
                    session=session,
                    chain_id=request_chain,
                    reason=error.message,
                    source=source,
                    status="FAILED",
                )
                await self._persist()
                raise
            for request in superseded:
                request.update(state="CANCELLED", error="绑定即将切换；原手动等待任务已取消")
        if session != binding.session:
            self.retired_details[f"{binding.slot}:{binding.session}"] = {
                "at": time.time(),
                "reason": f"session 已被 {session} 替代",
                "binding_id": binding.id,
            }
            self.retired[f"{binding.slot}:{binding.session}"] = (
                time.time() + self.settings.retired_session_ttl
            )
        old_session = binding.session
        binding.unavailable = ""
        binding.reserved = False
        binding.rotation = {}
        binding.session, binding.country = session, country
        binding.policy_version = self.quality.groups[binding.group_id]["version"]
        binding.node_id, binding.generation, binding.last_active = (
            node.id,
            binding.generation + 1,
            time.time(),
        )
        node.state, node.used, node.binding_id, node.generation = (
            IPState.IN_USE,
            True,
            binding.id,
            binding.generation,
        )
        node.reason = "会话独占"
        self.lifecycle.keys[f"{binding.id}:{binding.generation}"] = request_chain
        if old:
            self.lifecycle.follow(old, binding)
        retire = draining + ([old] if old else [])
        for previous in retire:
            if previous.state == IPState.RESERVED and not previous.used:
                previous.binding_id = None
                previous.state = IPState.FRESH if self.quality.eligible(previous) else IPState.QUARANTINED
                previous.reason = "未使用预留已解除"
                continue
            self.blacklist[previous.ip] = time.time() + self.settings.blacklist_ttl
            if mode == "graceful" and self._count_node(previous.id):
                previous.state, previous.drain_until = (
                    IPState.DRAINING,
                    time.time() + self.settings.drain_timeout,
                )
                previous.reason = "等待旧连接结束"
            else:
                self._retire(previous, "主动轮换", cause=source)
            if reservation_id and previous is old:
                self.rotation.bind_reservation(reservation_id, previous)
        self.lifecycle.add(
            "switched",
            node=node,
            binding=binding,
            old_ip=old.ip if old else None,
            old_slot_id=old.slot_id if old else None,
            old_session=old_session,
            old_country=old.country if old else "",
            source=source,
            status=mode,
        )
        if old and old.state == IPState.DRAINING:
            self.lifecycle.add("draining", node=old, binding=binding, session=old_session)
        self.event(
            "rotate",
            "出口已轮换",
            binding_id=binding.id,
            ip=node.ip,
            mode=mode,
            source=source,
            old_session=old_session,
            old_ip=old.ip if old else None,
            session=session,
            generation=binding.generation,
            group_id=node.group_id,
            account_id=node.account_id,
        )
        await self._persist()
        if mode == "hard":
            for previous in retire:
                self._close_node(previous.id)
        self.changed.notify_all()

    async def rotate(
        self,
        binding_id: str,
        mode: str = "graceful",
        new_session: str | None = None,
        expected: int | None = None,
        reservation_id: str = "",
        source: str = "manual",
    ):
        binding = self.bindings.get(binding_id)
        if not binding or binding.released:
            raise GatewayError("NOT_FOUND", "绑定不存在", 404)
        async with self.slot_locks.setdefault(binding.slot, asyncio.Lock()):
            async with self.changed:
                self._writable()
                if expected is not None and binding.generation != expected:
                    raise GatewayError("VERSION_CONFLICT", "绑定已变化，请刷新后重试")
                if new_session is not None and new_session == binding.session:
                    raise GatewayError("SESSION_UNCHANGED", "新 session 必须与当前 session 不同", 422)
                await self._switch(
                    binding, new_session or binding.session, mode, binding.country, reservation_id, source
                )
                return self.public_binding(binding)

    async def release(
        self,
        binding_id: str,
        mode: str = "graceful",
        expected: int | None = None,
        reservation_only: bool = False,
    ):
        binding = self.bindings.get(binding_id)
        if not binding or binding.released:
            raise GatewayError("NOT_FOUND", "绑定不存在", 404)
        async with self.slot_locks.setdefault(binding.slot, asyncio.Lock()):
            async with self.changed:
                self._writable()
                if expected is not None and binding.generation != expected:
                    raise GatewayError("VERSION_CONFLICT", "绑定已变化，请刷新")
                if reservation_only:
                    node = self.nodes.get(binding.node_id)
                    if not binding.reserved or not node or node.state != IPState.RESERVED or node.used:
                        raise GatewayError("RESERVATION_USED", "预留已被连接使用，未取消或更换，请刷新")
                self.dynamic.before_release(binding)
                self._release(binding, mode)
                await self._persist()
                if mode == "hard":
                    for node in self.nodes.values():
                        if node.binding_id == binding.id:
                            self._close_node(node.id)
                self.changed.notify_all()
        return {"binding_id": binding.id, "released": True}

    def _release(self, binding: Binding, mode: str, reason="管理员主动释放"):
        node = self.nodes.get(binding.node_id)
        release_action = (
            "unused_reservation"
            if node and node.state == IPState.RESERVED and not node.used
            else "used_exit"
            if node and node.state == IPState.IN_USE
            else "other"
        )
        self.lifecycle.add(
            "released",
            binding=binding,
            node=node,
            reason=reason,
            release_action=release_action,
            resource_action="return_after_drain",
        )
        binding.released = True
        binding.released_at, binding.release_reason = time.time(), reason
        self.retired_details[f"{binding.slot}:{binding.session}"] = {
            "at": time.time(),
            "reason": reason,
            "binding_id": binding.id,
        }
        binding.rotation = {}
        self.dynamic.last_commit.pop(binding.id, None)
        self.retired[f"{binding.slot}:{binding.session}"] = time.time() + self.settings.retired_session_ttl
        for node in self.nodes.values():
            if node.binding_id == binding.id and node.state == IPState.RESERVED and not node.used:
                node.binding_id = None
                node.state = IPState.FRESH if self.quality.eligible(node) else IPState.QUARANTINED
                node.reason = "未使用预留已取消，不消耗更换额度"
            if node.binding_id == binding.id and node.state in {IPState.IN_USE, IPState.DRAINING}:
                self.returning[node.id] = binding.id
                for record in self.rotation.pending():
                    if record["node_id"] == node.id:
                        record["state"] = "CANCELLED"
                if mode == "graceful" and self._count_node(node.id):
                    node.state, node.drain_until = IPState.DRAINING, time.time() + self.settings.drain_timeout
                else:
                    self._close_node(node.id)
                    self._return_node(node)
        self.event(
            "release",
            reason,
            binding_id=binding.id,
            group_id=binding.group_id,
            session=binding.session,
            source="manual",
        )

    def _return_node(self, node):
        """Release ownership without retiring the supplier resource or charging replacements."""
        self.lifecycle.add(
            "returned",
            node=node,
            binding=self.bindings.get(node.binding_id),
            reason="链接绑定已解除；原 IP 未更换，重新按本组准入参与分配",
        )
        node.used, node.binding_id, node.drain_until = False, None, None
        self.returning.pop(node.id, None)
        self.blacklist.pop(node.ip, None)
        node.state = IPState.FRESH if self.quality.eligible(node) else IPState.QUARANTINED
        node.reason = "释放后返回待分配池" if node.state == IPState.FRESH else "释放后保留原 IP，等待重新准入"

    async def reserve_dial(self, binding):
        async with self.lock:
            total = len(self.tunnels) + sum(self.dialing.values())
            count = sum(t["binding_id"] == binding.id for t in self.tunnels.values()) + self.dialing.get(
                binding.id, 0
            )
            if total >= self.settings.max_connections:
                raise GatewayError(
                    "GATEWAY_CONNECTION_LIMIT",
                    f"网关连接 {total}/{self.settings.max_connections}（含建连中）",
                    503,
                )
            if count >= self.settings.max_connections_per_binding:
                raise GatewayError(
                    "BINDING_CONNECTION_LIMIT",
                    f"单绑定连接 {count}/{self.settings.max_connections_per_binding}（含建连中）",
                    503,
                )
            self.dialing[binding.id] = self.dialing.get(binding.id, 0) + 1

    def release_dial(self, binding_id):
        if binding_id:
            self.dialing[binding_id] = max(0, self.dialing.get(binding_id, 1) - 1)
            if not self.dialing[binding_id]:
                self.dialing.pop(binding_id, None)

    async def attach(self, binding_id, node_id, generation, client, upstream, observation=None):
        async with self.lock:
            if len(self.tunnels) >= self.settings.max_connections:
                raise GatewayError(
                    "GATEWAY_CONNECTION_LIMIT",
                    f"网关连接 {len(self.tunnels)}/{self.settings.max_connections}，请退避重试",
                    503,
                )
            binding, node = self.bindings[binding_id], self.nodes[node_id]
            if (
                binding.released
                or binding.node_id != node_id
                or binding.generation != generation
                or node.state != IPState.IN_USE
            ):
                raise GatewayError("STALE_CONNECTION", "连接建立期间绑定已切换，请重连")
            if (
                sum(t["binding_id"] == binding_id for t in self.tunnels.values())
                >= self.settings.max_connections_per_binding
            ):
                raise GatewayError(
                    "BINDING_CONNECTION_LIMIT",
                    f"单绑定连接 {sum(t['binding_id'] == binding_id for t in self.tunnels.values())}/{self.settings.max_connections_per_binding}，请退避重试",
                    503,
                )
            if binding.connected_generation != generation:
                binding.connected_generation = generation
                self.lifecycle.add("connected", node=node, binding=binding)
                await self._persist()
            tunnel_id = uid()
            self.tunnels[tunnel_id] = {
                "id": tunnel_id,
                "binding_id": binding_id,
                "node_id": node_id,
                "generation": generation,
                "client": client,
                "upstream": upstream,
                "started_at": time.time(),
                "up": 0,
                "down": 0,
            }
            if observation is not None:
                self.observer.established(observation, node, binding)
                self.tunnels[tunnel_id]["observation"] = observation
            binding.last_active = time.time()
            self.rotation.connected(binding)
            return tunnel_id

    def traffic(self, tunnel_id, direction, size, at):
        if tunnel := self.tunnels.get(tunnel_id):
            tunnel[direction] += size
            node = self.nodes[tunnel["node_id"]]
            self.observer.add(direction, size, node.account_id, node.group_id)
            if observation := tunnel.get("observation"):
                observation[direction] += size
            self.bindings[tunnel["binding_id"]].last_active = at
            if direction == "up":
                self.bytes_up += size
            else:
                self.bytes_down += size

    async def detach(self, tunnel_id):
        async with self.changed:
            tunnel = self.tunnels.pop(tunnel_id, None)
            if not tunnel:
                return
            binding, node = self.bindings[tunnel["binding_id"]], self.nodes[tunnel["node_id"]]
            binding.last_detached = time.time()
            if node.state == IPState.DRAINING and not self._count_node(node.id):
                if node.id in self.returning:
                    self._return_node(node)
                else:
                    self._retire(node, "旧连接排空完成")
                if not self.storage_failed:
                    await self._persist()
            self.changed.notify_all()

    async def connection_success(self, node_id):
        self.nodes[node_id].fail_count = 0
        self.nodes[node_id].last_success_at = time.time()
        node = self.nodes[node_id]
        if (binding := self.bindings.get(node.binding_id)) and binding.node_id == node_id:
            binding.unavailable = ""

    async def connection_failure(self, node_id: str, kind: str) -> bool:
        async with self.changed:
            self._writable()
            node = self.nodes[node_id]
            if kind == "authentication":
                self.account_health[node.account_id] = {
                    "state": "authentication_error",
                    "message": "上游认证失败，请同步凭据",
                }
                self.event("error", "上游账号认证失败", account_id=node.account_id)
                await self._persist()
                return False
            if kind != "transport":
                return False
            if node.state != IPState.IN_USE:
                return True
            node.fail_count += 1
            if node.fail_count < self.settings.max_fail_count:
                return False
            binding = self.bindings[node.binding_id]
            if self.rotation.failover(binding.group_id):
                self.lifecycle.add("fault_retired", node=node, binding=binding, reason="上游连续传输失败")
                binding.unavailable = ""
                binding.node_id, binding.generation = None, binding.generation + 1
                binding.failovers += 1
                binding.last_detached = time.time()
            if not self.rotation.failover(binding.group_id):
                binding.unavailable = "原出口连续连接失败；固定模式保留 IP，请检查或显式更换"
                self.event(
                    "exit_unavailable",
                    binding.unavailable,
                    binding_id=binding.id,
                    group_id=binding.group_id,
                    ip=node.ip,
                )
                await self._persist()
                return False
            self._retire(node, "节点级连接故障")
            self.event("failover", "节点故障，原 session 可重连", binding_id=binding.id, ip=node.ip)
            await self._persist()
            self._close_node(node.id)
            self.changed.notify_all()
            return self.rotation.failover(binding.group_id)

    async def sync_all(self):
        for index, account_id in enumerate(list(self.accounts)):
            if index:
                await asyncio.sleep(0.35)
            await self.sync_account(account_id)
            await self.sync_usage(account_id)

    async def sync_usage(self, account_id, force=False):
        async with self.usage_locks.setdefault(account_id, asyncio.Lock()):
            account = self.accounts.get(account_id)
            provider = self.providers.get(account_id)
            if not account or not provider or not hasattr(provider, "usage"):
                return {"available": False, "error": "检测源暂不支持套餐用量查询"}
            old = self.supplier_usage.get(account_id, {})
            if time.time() - old.get("last_attempt", 0) < (15 if force else 300):
                return public_usage(old)
            try:
                async with asyncio.timeout(40):
                    snapshot = await provider.usage()
            except (ProviderError, TimeoutError, ValueError, KeyError, TypeError):
                snapshot = {
                    **old,
                    "last_attempt": time.time(),
                    "error": "套餐用量查询失败；检查 API 权限或稍后重试",
                }
            async with self.changed:
                self._writable()
                if (
                    self.accounts.get(account_id) is not account
                    or self.providers.get(account_id) is not provider
                ):
                    return {"available": False, "error": "账号配置已变化，请重新同步"}
                self.supplier_usage[account_id] = snapshot
                await self._persist()
            return public_usage(snapshot)

    async def sync_account(self, account_id: str):
        async with self.account_locks[account_id]:
            if account_id not in self.accounts:
                return
            if any(
                job.account_id == account_id
                and job.state in {"SUBMITTING", "PROCESSING", "RECONCILING", "UNCERTAIN"}
                for job in self.replacements.values()
            ):
                return
            try:
                rows = await self.providers[account_id].list_nodes()
            except ProviderError as error:
                self.account_health[account_id] = {
                    "state": "error",
                    "message": str(error),
                    "synced_at": time.time(),
                }
                self.event("error", "账号同步失败", account_id=account_id, status=error.status)
                return
            async with self.changed:
                self._writable()
                candidates = self._ingest(account_id, rows)
                self.account_health[account_id] = {
                    "state": "healthy",
                    "message": "同步正常",
                    "synced_at": time.time(),
                }
                self.event("sync", "账号同步完成", account_id=account_id, count=len(rows))
                await self._persist()
            await self._probe_candidates(candidates)

    def _ingest(self, account_id: str, rows: list[dict]):
        by_ip = {
            n.ip: n
            for n in self.nodes.values()
            if n.account_id == account_id
            and (
                n.state != IPState.ARCHIVED
                or (
                    n.binding_id in self.bindings
                    and not self.bindings[n.binding_id].released
                    and self.bindings[n.binding_id].node_id == n.id
                )
            )
        }
        present = {row["proxy_address"] for row in rows if row.get("proxy_address")}
        for ip, node in by_ip.items():
            if ip not in present and node.state not in {IPState.REPLACING, IPState.ARCHIVED}:
                if (
                    node.binding_id
                    and (binding := self.bindings.get(node.binding_id))
                    and binding.node_id == node.id
                ):
                    binding.unavailable = "供应商已移除原出口；请显式更换"
                    if binding.id in self.dynamic.pending:
                        self.dynamic.admission_error(node, "供应商已移除待准入出口")
                    elif self.rotation.failover(binding.group_id):
                        binding.unavailable = ""
                        binding.node_id, binding.generation = None, binding.generation + 1
                    binding.last_detached = time.time()
                self._retire(node, "供应商已移除节点", replace=False)
                self._close_node(node.id)
        candidates = []
        for row in rows:
            ip = row.get("proxy_address")
            if not ip:
                continue
            node = by_ip.get(ip)
            if not node:
                node = Node(
                    uid(),
                    account_id,
                    str(row.get("id", ip)),
                    ip,
                    int(row["port"]),
                    row["username"],
                    row["password"],
                    row.get("country_code", ""),
                )
                self.nodes[node.id] = node
                self.quality.ensure_node(node)
            node.username, node.password = row["username"], row["password"]
            if pending := self.dynamic.pending.get(node.binding_id):
                if pending.get("candidate_id") == node.id:
                    pending["supplier_valid"] = bool(row.get("valid"))
            if not row.get("valid", False):
                if node.state == IPState.FRESH:
                    node.state = IPState.QUARANTINED
                node.reason = "供应商标记不可用"
                continue
            if node.state == IPState.ARCHIVED and node.binding_id in self.bindings:
                binding = self.bindings[node.binding_id]
                if not binding.released and binding.node_id == node.id and binding.id in self.dynamic.pending:
                    node.state = IPState.QUARANTINED
                elif not binding.released and binding.node_id == node.id:
                    node.state = IPState.RESERVED if binding.reserved else IPState.IN_USE
                    node.reason = "原出口重新出现，恢复保留绑定"
                    binding.unavailable = ""
            if node.state == IPState.QUARANTINED and not node.used and not node.replacement_error:
                if not self.config.probe_enabled:
                    node.reason = "尚未验证（当前部署暂停主动测活）"
                candidates.append(node.id)
        return candidates

    def collision(self, node):
        return self.blacklist.get(node.ip, 0) > time.time() or any(
            n.id != node.id
            and n.ip == node.ip
            and n.state
            in {IPState.FRESH, IPState.RESERVED, IPState.IN_USE, IPState.DRAINING, IPState.REPLACING}
            for n in self.nodes.values()
        )

    async def _probe_candidates(self, ids: list[str]):
        grouped = {}
        for id in ids:
            node = self.nodes[id]
            if node.group_id != "default" and id not in self.quality.busy and id not in self.probing:
                grouped.setdefault(node.group_id, []).append(id)
        for group_id, members in grouped.items():
            if group_id not in self.quality.running and self.quality.groups.get(group_id, {}).get("enabled"):
                await self.quality.run(group_id, members[:100])
        ids = [id for id in ids if self.nodes[id].group_id == "default"]
        if not self.config.probe_enabled:
            return
        semaphore = asyncio.Semaphore(self.settings.probe_concurrency)
        results = []

        async def check(node_id):
            node = self.nodes[node_id]
            if node.account_id not in self.accounts or node.state not in {IPState.FRESH, IPState.QUARANTINED}:
                return
            if node_id in self.probing:
                return
            self.probing.add(node_id)
            try:
                async with semaphore:
                    if not self.config.probe_enabled:
                        return
                    started = time.monotonic()
                    healthy = await self.probe(copy.copy(node), self.settings.probe_timeout)
                    elapsed_ms = (time.monotonic() - started) * 1000
                    self.observer.probe(node, healthy, elapsed_ms)
                results.append((node_id, healthy))
                async with self.changed:
                    if not self.config.probe_enabled:
                        return
                    if (
                        node.state not in {IPState.FRESH, IPState.QUARANTINED}
                        or node.used
                        or node.replacement_error
                    ):
                        return
                    collision = self.blacklist.get(node.ip, 0) > time.time() or any(
                        other.id != node.id
                        and other.ip == node.ip
                        and other.state
                        in {
                            IPState.FRESH,
                            IPState.RESERVED,
                            IPState.IN_USE,
                            IPState.DRAINING,
                            IPState.REPLACING,
                        }
                        for other in self.nodes.values()
                    )
                    node.checked_at = time.time()
                    node.latency_ms = round(elapsed_ms, 2)
                    if healthy:
                        node.last_success_at = time.time()
                    previous_state = node.state
                    node.state = IPState.FRESH if healthy and not collision else IPState.QUARANTINED
                    node.reason = (
                        "待分配"
                        if node.state == IPState.FRESH
                        else ("冷却或跨账号重复" if collision else "健康检查未通过")
                    )
                    dynamic_admission = node.binding_id in self.dynamic.pending
                    self.rotation.restore(node)
                    if previous_state != node.state and not dynamic_admission:
                        self.lifecycle.add("admission", node=node, status=node.state, reason=node.reason)
                    node.fail_count = 0 if healthy else node.fail_count + 1
                    await self._persist()
                    self.changed.notify_all()
            finally:
                self.probing.discard(node_id)

        await asyncio.gather(*(check(node_id) for node_id in ids))
        # A correlated outage is not evidence that every supplier IP needs replacement.
        # Only recycle repeatedly failing unused nodes when a peer probe succeeded.
        async with self.changed:
            if not self.config.probe_enabled:
                return
            changed = False
            if any(healthy for _, healthy in results):
                for node_id, healthy in results:
                    node = self.nodes[node_id]
                    if (
                        not healthy
                        and node.state == IPState.QUARANTINED
                        and not node.used
                        and not node.binding_id
                        and node.reason == "健康检查未通过"
                        and node.fail_count >= self.settings.max_fail_count
                    ):
                        self._retire(node, "独立节点重复测活失败")
                        changed = True
            elif results:
                self.event("probe", "本轮没有成功探测，保持隔离，未批量消耗替换额度")
                changed = True
            if changed:
                await self._persist()
                self.changed.notify_all()

    async def maintain(self):
        now = time.time()
        async with self.changed:
            self._writable()
            for node in self.nodes.values():
                if node.state == IPState.DRAINING and (
                    not self._count_node(node.id) or now >= node.drain_until
                ):
                    self._close_node(node.id)
                    if node.id in self.returning:
                        self._return_node(node)
                    else:
                        self._retire(node, "排空完成或到期")
            self.blacklist = {ip: expiry for ip, expiry in self.blacklist.items() if expiry > now}
            self.retired_details = {
                key: data for key, data in self.retired_details.items() if self.retired.get(key, 0) > now
            }
            self.rotation.previews = {
                key: value for key, value in self.rotation.previews.items() if value["expires_at"] > now
            }
            self.retired = {key: expiry for key, expiry in self.retired.items() if expiry > now}
            self.idempotency = {key: item for key, item in self.idempotency.items() if item["expires"] > now}
            self._prune_history(now)
            await self._persist()
            self.changed.notify_all()

    def _prune_history(self, now):
        policy = self.observer.retention
        self.lifecycle.cutoff = now - policy.trace_days * 86400
        self.lifecycle.related = {
            key: chain
            for key, chain in self.lifecycle.related.items()
            if key in self.nodes and self.nodes[key].state != IPState.ARCHIVED
        }
        active = {f"{b.id}:{b.generation}" for b in self.bindings.values() if not b.released}
        self.lifecycle.keys = {key: chain for key, chain in self.lifecycle.keys.items() if key in active}
        self.idempotency = {key: item for key, item in self.idempotency.items() if item["expires"] > now}
        protected = {item["id"] for item in self.idempotency.values() if item["expires"] > now}
        self.operations = {
            key: op
            for key, op in self.operations.items()
            if key in protected
            or op.state not in {"COMPLETED", "FAILED"}
            or not op.completed_at
            or op.completed_at >= now - policy.operation_days * 86400
        }
        self.events = [
            event for event in self.events if event.get("at", now) >= now - policy.event_days * 86400
        ][-1000:]

    async def cleanup_history(self):
        # Separate locks: observation writes never hold the critical scheduling lock.
        async with self.changed:
            self._writable()
            before = len(self.operations)
            self._prune_history(time.time())
            await self._persist()
        async with self.observer.lock:
            await self.observer.compact(time.time())
            self.observer.last_compact = time.time()
        return {"removed_operations": before - len(self.operations), **await self.observer.storage_status()}

    async def _observe(self):
        while True:
            await self.observer.flush(self)
            await asyncio.sleep(5)

    async def _periodic(self):
        last_sync, last_probe = time.time(), time.time()
        while True:
            await asyncio.sleep(10)
            await self.maintain()
            await self.rotation.tick()
            await self.quality.due()
            now = time.time()
            if now - last_sync >= self.settings.sync_interval:
                await self.sync_all()
                last_sync = now
            if now - last_probe >= self.settings.probe_interval:
                ids = [
                    n.id
                    for n in self.nodes.values()
                    if n.state in {IPState.FRESH, IPState.QUARANTINED} and not n.used
                ]
                await self._probe_candidates(ids)
                last_probe = now

    async def _replacement_loop(self):
        while True:
            await asyncio.sleep(1.5)
            jobs = [
                job
                for job in self.replacements.values()
                if job.state in {"QUEUED", "PROCESSING", "RECONCILING"}
            ]
            by_account = {}
            for job in sorted(jobs, key=lambda item: item.created_at):
                by_account.setdefault(job.account_id, job)
            await asyncio.gather(
                *(
                    self.process_replacement(job.id)
                    for job in by_account.values()
                    if job.state in {"QUEUED", "PROCESSING", "RECONCILING"} and job.retry_at <= time.time()
                )
            )

    async def process_replacement(self, job_id: str):
        job = self.replacements[job_id]
        async with self.account_locks[job.account_id]:
            if job.state not in {"QUEUED", "PROCESSING", "RECONCILING"}:
                return
            if any(
                other.account_id == job.account_id
                and other.id != job.id
                and (
                    other.state == "UNCERTAIN"
                    or (
                        other.state in {"QUEUED", "SUBMITTING", "PROCESSING", "RECONCILING"}
                        and other.created_at < job.created_at
                    )
                )
                for other in self.replacements.values()
            ):
                return
            provider, node = self.providers[job.account_id], self.nodes[job.node_id]
            in_place = self.dynamic.active_for_job(job) is not None
            try:
                if job.state == "QUEUED":
                    account, counts = self.accounts[job.account_id], self.counters[job.account_id]
                    async with self.changed:
                        try:
                            self.rotation.check_job(job)
                            if not job.reservation_id:
                                job.reservation_id = self.rotation.reserve(node, job.cause, job=job)
                                self.rotation.ledger[job.reservation_id]["committed"] = True
                            await self._persist()
                        except GatewayError as error:
                            job.state, job.error = "BUDGET_BLOCKED", error.message
                            if in_place:
                                job.state = "FAILED"
                                self.dynamic.fail(job, safe=True)
                            await self._persist()
                            return
                    rows = await provider.list_nodes()
                    if node.ip not in {row["proxy_address"] for row in rows}:
                        if node.group_id != "default" or in_place:
                            async with self.changed:
                                job.state, job.error = (
                                    "UNCERTAIN",
                                    "供应商外部变更，无法唯一继承分组席位；需对账",
                                )
                                self.dynamic.fail(job, safe=False)
                                await self._persist()
                            return
                        async with self.changed:
                            job.state, node.state = "COMPLETED", IPState.ARCHIVED
                            self.rotation.ledger[job.reservation_id]["state"] = "CANCELLED"
                            job.reason = "旧 IP 已从供应商列表移除；未提交替换，补货归属未知"
                            ids = self._ingest(job.account_id, rows)
                            await self._persist()
                        await self._probe_candidates(ids)
                        return
                    if (
                        job.selection
                        and job.selection["type"] == "country"
                        and not job.selection.get("country_code")
                    ):
                        async with self.changed:
                            job.state, job.error = "FAILED", "旧 IP 国家未知，未提交替换"
                            self.dynamic.fail(job, safe=True)
                            await self._persist()
                        return
                    if job.fallback_enabled is not None and job.selection and job.selection["type"] != "any":
                        if job.preflight_attempts >= 3:
                            async with self.changed:
                                job.state, job.error = (
                                    "FAILED",
                                    "预检已达到 3 次上限；未提交真实替换，请手动核对",
                                )
                                self.dynamic.fail(job, safe=True)
                                await self._persist()
                            return

                        async def save_preflight():
                            async with self.changed:
                                await self._persist()

                        try:
                            job.preflight_attempts += 1
                            await save_preflight()
                            ready = await preflight(provider, node.ip, job.selection, job, save_preflight)
                        except (
                            GatewayError,
                            ProviderError,
                            TimeoutError,
                            ValueError,
                            KeyError,
                            TypeError,
                            AttributeError,
                        ) as error:
                            detail = (
                                "预检等待超时"
                                if isinstance(error, TimeoutError)
                                else f"预检服务错误 HTTP {error.status}"
                                if isinstance(error, ProviderError)
                                else error.message
                                if isinstance(error, GatewayError)
                                else "预检返回格式异常"
                            )
                            async with self.changed:
                                retryable = (
                                    isinstance(error, TimeoutError)
                                    or isinstance(error, ProviderError)
                                    and (error.status == 0 or error.status == 429 or error.status >= 500)
                                    or isinstance(error, GatewayError)
                                    and bool(job.preflight_id)
                                )
                                retry = retryable and job.preflight_attempts < 3
                                job.state = "QUEUED" if retry else "FAILED"
                                job.retry_at = (
                                    time.time() + (5 if job.preflight_attempts == 1 else 15) if retry else 0
                                )
                                job.error = f"指定规则预检未确认（{detail}）；未提交真实替换" + (
                                    f"；等待预检重试 {job.preflight_attempts + 1}/3"
                                    if retry
                                    else "；预检已停止"
                                )
                                if not retry:
                                    self.dynamic.fail(job, safe=True)
                                await self._persist()
                            return
                        if not ready:
                            async with self.changed:
                                if not job.fallback_enabled:
                                    job.state, job.error = "FAILED", "指定规则库存不足；随机兜底已关闭"
                                    self.dynamic.fail(job, safe=True)
                                    await self._persist()
                                    return
                                job.selection = dict(RANDOM)
                                job.fallback_reason = NO_STOCK
                                await self._persist()
                    async with self.changed:
                        self._writable()
                        try:
                            self.rotation.check_job(job)
                        except GatewayError as error:
                            job.state, job.error = "BUDGET_BLOCKED", error.message
                            if in_place:
                                job.state = "FAILED"
                                self.dynamic.fail(job, safe=True)
                            await self._persist()
                            return
                        self.quality.debit(node, job)
                        job.before_ips = [row["proxy_address"] for row in rows]
                        job.state, job.attempts = "SUBMITTING", job.attempts + 1
                        job.error, job.retry_at = None, 0
                        counts["submitted"] += 1
                        self.rotation.submitted(job)
                        await self._persist()
                        if in_place:
                            self._close_node(node.id)
                    country = (
                        node.country
                        if account.replacement_country == "original"
                        else account.replacement_country
                    )
                    if job.selection and (job.fallback_enabled is None or job.selection["type"] != "any"):
                        response = await provider.replace_selected(node.ip, job.selection, dry_run=False)
                        provider_id = str(response["id"])
                    else:
                        provider_id = await provider.replace(
                            node.ip, "any" if job.selection else country or "any"
                        )
                    async with self.changed:
                        job.provider_id, job.state = provider_id, "PROCESSING"
                        await self._persist()
                if job.state == "PROCESSING":
                    status = await provider.replacement(job.provider_id)
                    if status.get("state") == "failed":
                        async with self.changed:
                            if (
                                not in_place
                                and job.fallback_enabled
                                and job.selection
                                and job.selection["type"] != "any"
                                and status.get("error_code") == NO_STOCK
                                and status.get("proxies_removed") == 0
                                and status.get("proxies_added") == 0
                            ):
                                self.lifecycle.job(
                                    job, status="FAILED", reason="供应商确认指定库存不足，未更换 IP"
                                )
                                if job.reservation_id:
                                    old_reservation = self.rotation.ledger[job.reservation_id]
                                    try:
                                        rid = self.rotation.reserve(
                                            node, old_reservation["kind"], old_reservation["binding_id"]
                                        )
                                    except GatewayError as error:
                                        job.state, job.error = "FAILED", "随机兜底未提交：" + error.message
                                        await self._persist()
                                        return
                                    job.reservation_id = rid
                                    self.rotation.ledger[rid]["committed"] = True
                                job.failed_provider_id = job.provider_id or ""
                                job.provider_id, job.selection = None, dict(RANDOM)
                                job.state, job.error, job.retry_at = "QUEUED", None, 0
                                job.fallback_reason = NO_STOCK
                            else:
                                job.state, job.error = "FAILED", "供应商拒绝替换"
                                if status.get("error_code") == NO_STOCK:
                                    job.error += "：指定库存不足或影响范围未明确"
                                no_effect = status.get("proxies_removed") == status.get("proxies_added") == 0
                                if in_place and not no_effect:
                                    job.state = "UNCERTAIN"
                                self.dynamic.fail(job, safe=no_effect)
                            await self._persist()
                        return
                    if status.get("state") != "completed":
                        job.retry_at = time.time() + 1.5
                        return
                    async with self.changed:
                        if in_place and not (
                            status.get("proxies_removed") == status.get("proxies_added") == 1
                        ):
                            job.state, job.error = "UNCERTAIN", "供应商未确认一进一出；请人工核对"
                            self.dynamic.fail(job, safe=False)
                            await self._persist()
                            return
                        job.state, job.error = "RECONCILING", None
                        await self._persist()
                if job.state == "RECONCILING":
                    rows = await provider.list_nodes()
                    before = set(job.before_ips)
                    after = {row["proxy_address"] for row in rows}
                    candidates = [row for row in rows if row["proxy_address"] not in before]
                    if node.ip in after:
                        job.retry_at = time.time() + 5
                        return
                    if len(candidates) != 1 or (in_place and before - after != {node.ip}):
                        async with self.changed:
                            job.state, job.error = "UNCERTAIN", "列表差集无法唯一确定替换结果，请人工核对"
                            self.dynamic.fail(job, safe=False)
                            await self._persist()
                        return
                    async with self.changed:
                        node.state, job.state = IPState.ARCHIVED, "COMPLETED"
                        self.counters[job.account_id]["completed"] += 1
                        ids = self._ingest(job.account_id, rows)
                        candidate_ip = candidates[0]["proxy_address"]
                        job.new_ip = candidate_ip
                        fresh = next(
                            n
                            for n in self.nodes.values()
                            if n.account_id == job.account_id
                            and n.ip == candidate_ip
                            and n.state != IPState.ARCHIVED
                        )
                        self.quality.inherit(node, fresh)
                        self.lifecycle.inherit(node, fresh)
                        if in_place:
                            self.dynamic.candidate(job, fresh, bool(candidates[0].get("valid")))
                            if not candidates[0].get("valid"):
                                self.dynamic.admission_error(fresh, "供应商标记新 IP 不可用")
                            elif fresh.group_id == "default" and not self.config.probe_enabled:
                                self.dynamic.admission_error(fresh, "主动测活已关闭，请在 IP 资源池手动探测")
                        elif (
                            job.rebind_id
                            and (binding := self.bindings.get(job.rebind_id))
                            and not binding.released
                            and binding.node_id == node.id
                        ):
                            binding.node_id, binding.generation = fresh.id, binding.generation + 1
                            binding.unavailable = "新 IP 等待质量准入"
                            fresh.binding_id = binding.id
                            self.lifecycle.add(
                                "switched",
                                node=fresh,
                                binding=binding,
                                session=binding.session,
                                generation=binding.generation,
                                old_ip=node.ip,
                                old_slot_id=node.slot_id,
                                source="manual_supplier",
                                status="QUARANTINED",
                            )
                        collision = self.blacklist.get(fresh.ip, 0) > time.time() or any(
                            n.id != fresh.id and n.ip == fresh.ip and n.state != IPState.ARCHIVED
                            for n in self.nodes.values()
                        )
                        if collision and job.redraws < self.settings.max_collision_redraws:
                            if not job.reservation_id:
                                self._retire(fresh, "替换结果命中冷却或重复", redraws=job.redraws + 1)
                                self.counters[job.account_id]["redraws"] += 1
                            else:
                                fresh.reason = "命中冷却/重复，已停止自动重抽；请人工核对"
                                if in_place:
                                    self.dynamic.admission_error(fresh, fresh.reason)
                        elif collision:
                            fresh.reason = "重复重抽上限，需人工核对"
                        self.event(
                            "replacement", "上游替换完成", account_id=job.account_id, group_id=job.group_id
                        )
                        await self._persist()
                        self.changed.notify_all()
                    await self._probe_candidates(ids)
            except ProviderError as error:
                async with self.changed:
                    if job.state == "SUBMITTING":
                        job.state = (
                            "UNCERTAIN"
                            if error.uncertain
                            else ("QUEUED" if error.status == 429 and not job.reservation_id else "FAILED")
                        )
                    job.error, job.retry_at = str(error), time.time() + max(5, error.retry_after)
                    job.updated_at = time.time()
                    if in_place and job.state in {"UNCERTAIN", "FAILED"}:
                        # Only explicit client-side rejection is safe; ambiguous POST responses are held.
                        if job.state == "FAILED" and error.status not in {400, 401, 403, 404, 422, 429}:
                            job.state = "UNCERTAIN"
                        self.dynamic.fail(job, safe=job.state == "FAILED")
                    self.event("error", "替换任务受阻", account_id=job.account_id, status=error.status)
                    await self._persist()
            except (ValueError, KeyError, TypeError, AttributeError):
                if not in_place:
                    raise
                async with self.changed:
                    safe = job.state == "QUEUED"
                    job.state = "FAILED" if safe else "UNCERTAIN"
                    job.error = "供应商响应格式异常，请核对任务"
                    self.dynamic.fail(job, safe=safe)
                    await self._persist()

    async def operation(self, key: str, fingerprint: str, kind: str, action, group_id="", account_id=""):
        async with self.changed:
            self._writable()
            if existing := self.idempotency.get(key):
                if existing["fingerprint"] != fingerprint:
                    raise GatewayError("IDEMPOTENCY_CONFLICT", "同一幂等键不能提交不同操作")
                return self.operations[existing["id"]]
            op = Operation(uid(), kind, group_id=group_id, account_id=account_id)
            self.operations[op.id] = op
            self.idempotency[key] = {"id": op.id, "fingerprint": fingerprint, "expires": time.time() + 86400}
            await self._persist()

        async def run():
            try:
                op.state = "RUNNING"
                result = await action()
                async with self.changed:
                    op.result, op.state, op.completed_at = result or {}, "COMPLETED", time.time()
                    await self._persist()
            except (GatewayError, TimeoutError) as error:
                async with self.changed:
                    op.error = error.message if isinstance(error, GatewayError) else "等待超时"
                    op.state, op.completed_at = "FAILED", time.time()
                    await self._persist()
            except Exception as error:
                log.error("Operation failed: %s", type(error).__name__)
                async with self.changed:
                    op.error, op.state, op.completed_at = (
                        "操作失败，请查看事件并核对现状",
                        "FAILED",
                        time.time(),
                    )
                    await self._persist()

        self.spawn(run())
        return op

    def public_binding(self, binding: Binding):
        node = self.nodes.get(binding.node_id)
        generations = []
        for n in self.nodes.values():
            if n.binding_id == binding.id and n.state in {IPState.RESERVED, IPState.IN_USE, IPState.DRAINING}:
                generations.append(
                    {
                        "node_id": n.id,
                        "ip": n.ip,
                        "generation": n.generation,
                        "state": n.state,
                        "connections": self._count_node(n.id),
                        "drain_until": n.drain_until,
                    }
                )
        return {
            **asdict(binding),
            "ip": node.ip if node and node.state in {IPState.RESERVED, IPState.IN_USE} else None,
            "account_id": node.account_id if node else None,
            "connections": sum(t["binding_id"] == binding.id for t in self.tunnels.values()),
            "rotation_status": self.rotation.binding_status(binding),
            "dynamic_switch": self.dynamic.public(binding),
            "status": "RELEASED"
            if binding.released
            else (
                "UNAVAILABLE"
                if binding.unavailable
                else (
                    "RESERVED"
                    if node and node.state == IPState.RESERVED
                    else "ACTIVE"
                    if node and node.state == IPState.IN_USE and self._count_node(node.id)
                    else "RETAINED"
                    if node and node.state == IPState.IN_USE
                    else "WAITING"
                )
            ),
            "generations": generations,
        }

    def stats(self, account_id="", group_id=""):
        nodes = [
            n
            for n in self.nodes.values()
            if n.state != IPState.ARCHIVED
            and (not account_id or n.account_id == account_id)
            and (not group_id or n.group_id == group_id)
        ]

        def matches(binding):
            node = self.nodes.get(binding.node_id)
            return (not group_id or binding.group_id == group_id) and (
                not account_id or (node and node.account_id == account_id)
            )

        bindings = [b for b in self.bindings.values() if matches(b)]
        tunnels = [t for t in self.tunnels.values() if matches(self.bindings[t["binding_id"]])]
        counts = Counter(n.state for n in nodes)
        countries = {}
        for node in nodes:
            item = countries.setdefault(node.country, {"total": 0, "fresh": 0, "in_use": 0})
            item["total"] += 1
            item["fresh"] += self._fresh_eligible(node)
            item["in_use"] += node.state == IPState.IN_USE
        return {
            "total_ips": len(nodes),
            "fresh_count": sum(self._fresh_eligible(n) for n in nodes),
            "in_use_count": counts[IPState.IN_USE],
            "reserved_count": counts[IPState.RESERVED],
            "draining_count": counts[IPState.DRAINING],
            "quarantined_count": counts[IPState.QUARANTINED],
            "replacing_count": counts[IPState.REPLACING],
            "blacklist_count": sum(expiry > time.time() for expiry in self.blacklist.values()),
            "active_bindings": sum(not b.released for b in bindings),
            "binding_limit": self.active_limit(),
            "active_connections": len(tunnels),
            "retained_bindings": sum(
                not b.released and not b.reserved and not any(t["binding_id"] == b.id for t in tunnels)
                for b in bindings
            ),
            "waiters": self.waiters,
            "countries": countries,
            "bytes_up": self.bytes_up,
            "bytes_down": self.bytes_down,
            "started_at": self.started_at,
            "ready": self.ready and not self.storage_failed,
            "probe_enabled": self.config.probe_enabled,
            "at": time.time(),
            "replacement_pending": sum(
                j.state not in {"COMPLETED", "FAILED", "CANCELLED"}
                and (not account_id or j.account_id == account_id)
                and (not group_id or j.group_id == group_id)
                for j in self.replacements.values()
            ),
            "budget_blocked_count": sum(
                j.state == "BUDGET_BLOCKED"
                and (not account_id or j.account_id == account_id)
                and (not group_id or j.group_id == group_id)
                for j in self.replacements.values()
            ),
        }

    def _fresh_eligible(self, node):
        return (
            node.state == IPState.FRESH
            and self.accounts[node.account_id].enabled
            and self.account_health.get(node.account_id, {}).get("state") == "healthy"
            and not node.binding_id
            and not any(r["node_id"] == node.id for r in self.rotation.pending())
            and self.blacklist.get(node.ip, 0) <= time.time()
            and self.quality.eligible(node)
        )

    def public_accounts(self):
        return [
            {
                **asdict(account),
                "configured": bool(self.config.key(account)),
                "health": self.account_health.get(
                    account.account_id, {"state": "pending", "message": "等待同步"}
                ),
                "node_count": sum(
                    n.account_id == account.account_id and n.state != IPState.ARCHIVED
                    for n in self.nodes.values()
                ),
                "fresh_count": sum(
                    n.account_id == account.account_id and self._fresh_eligible(n)
                    for n in self.nodes.values()
                ),
                "usage": self.counters[account.account_id],
                "budget_source": "本地安全限制，独立于供应商套餐额度",
                "supplier_usage": public_usage(self.supplier_usage.get(account.account_id)),
            }
            for account in self.accounts.values()
        ]

    async def update_settings(self, values: dict):
        enabled_before = self.config.probe_enabled
        async with self.changed:
            self._writable()
            for key, value in values.items():
                if key == "probe_enabled":
                    self.config.probe_enabled = value
                elif key == "connection_log_mode":
                    self.observer.mode = value
                elif key == "retention":
                    self.observer.retention = RetentionPolicy.model_validate(value)
                elif key == "quality_daily_limit":
                    self.quality.global_daily_limit = value
                else:
                    setattr(self.settings, key, value)
            self.event("settings", "网关设置已更新", fields=list(values))
            await self._persist()
            self.changed.notify_all()
        if self.config.probe_enabled and not enabled_before:
            ids = [
                node.id
                for node in self.nodes.values()
                if node.state in {IPState.FRESH, IPState.QUARANTINED} and not node.used
            ]
            self.spawn(self._probe_candidates(ids), "probe-on-enable")
        return {**self.settings.public(), "probe_enabled": self.config.probe_enabled}

    async def save_account(self, account, key: str | None = None):
        # Caller serializes configuration edits. Secret is persisted before publishing its reference.
        if key:
            path = self.config.config_dir / "secrets.json"
            secrets = json.loads(path.read_text()) if path.exists() else {}
            secrets[account.account_id] = key
            await asyncio.to_thread(private_json, path, secrets)
        accounts = {**self.accounts, account.account_id: account}
        await asyncio.to_thread(
            private_json, self.config.config_dir / "accounts.json", [asdict(a) for a in accounts.values()]
        )
        self.reload_accounts()

    def _cleanup_removed_account_files(self):
        path = self.config.config_dir / "accounts.json"
        accounts = [a for a in self.config.accounts() if a.account_id not in self.removed_accounts]
        private_json(path, [asdict(a) for a in accounts])
        path = self.config.config_dir / "secrets.json"
        if path.exists():
            keys = json.loads(path.read_text())
            private_json(path, {k: v for k, v in keys.items() if k not in self.removed_accounts})

    def account_idle_error(self, account_id):
        if self.rotation.pending(account=account_id):
            return "账号仍有预留替换额度，请先取消或完成任务"
        ids = {n.id for n in self.nodes.values() if n.account_id == account_id}
        if any(not b.released and b.node_id in ids for b in self.bindings.values()) or any(
            t["node_id"] in ids for t in self.tunnels.values()
        ):
            return "账号仍有活跃绑定或连接，请先结束业务"
        if any(
            j.account_id == account_id and j.state not in {"COMPLETED", "FAILED"}
            for j in self.replacements.values()
        ):
            return "账号仍有替换或待核对任务，请先处理"
        if (
            ids & (self.probing | self.quality.busy)
            or self.ipquality.running in ids
            or self.ping0.running in ids
        ):
            return "账号资源仍在检测，请稍后重试"
        if any(
            n.id in ids and n.state in {IPState.RESERVED, IPState.IN_USE, IPState.DRAINING, IPState.REPLACING}
            for n in self.nodes.values()
        ):
            return "账号资源仍在使用、排空或替换中"
        return None

    async def remove_account(self, account_id):
        # Caller holds config_lock; account lock serializes against sync/replacement.
        if account_id not in self.accounts and account_id not in self.removed_accounts:
            raise GatewayError("NOT_FOUND", "账号不存在", 404)
        async with self.account_locks.setdefault(account_id, asyncio.Lock()):
            async with self.changed:
                self._writable()
                account = self.accounts.get(account_id)
                if account and account.enabled:
                    raise GatewayError("ACCOUNT_ENABLED", "请先暂停该账号的新分配，再删除", 409)
                if problem := self.account_idle_error(account_id):
                    raise GatewayError("ACCOUNT_BUSY", problem, 409)
                self.removed_accounts[account_id] = {
                    "name": account.name if account else self.removed_accounts[account_id]["name"],
                    "at": time.time(),
                }
                archived = 0
                for node in self.nodes.values():
                    if node.account_id == account_id:
                        archived += node.state != IPState.ARCHIVED
                        node.state, node.reason = IPState.ARCHIVED, "上游账号已从本网关移除"
                        node.username = node.password = ""
                self.event("account", "上游账号已从本地移除；未调用供应商替换或退订", account_id=account_id)
                # Persist the deletion marker first: a crash cannot re-admit this account.
                await self._persist()
                self.accounts.pop(account_id, None)
                self.providers.pop(account_id, None)
                self.account_health.pop(account_id, None)
                self.changed.notify_all()
            warning = None
            try:
                await asyncio.to_thread(self._cleanup_removed_account_files)
            except OSError:
                warning = "账号已移除，本地密钥文件清理未完成；重启时会重试清理"
            return {
                "account_id": account_id,
                "removed": True,
                "archived_nodes": archived,
                "replace_calls": 0,
                "warning": warning,
            }
