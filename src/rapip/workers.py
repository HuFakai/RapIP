"""Bounded generated workers, lazy ordinary links, and explicit unused IP reservations."""

from __future__ import annotations

import random
import time
from dataclasses import asdict

from .models import Binding, GatewayError, IPState, Route, uid


class Workers:
    def __init__(self, gateway):
        self.g = gateway
        self.rules = {}
        self.pending = {}
        self.receipts = {}

    def load(self, state):
        self.rules = state.get("rules", {})
        self.pending = state.get("pending", {})
        self.receipts = state.get("receipts", {})

    def snapshot(self):
        return {"rules": self.rules, "pending": self.pending, "receipts": self.receipts}

    def capacity(self, group):
        count = sum(n.group_id == group and n.state != IPState.ARCHIVED for n in self.g.nodes.values())
        return max(count, self.rules.get(group, {}).get("high_water", 0))

    def public(self, group):
        capacity = self.capacity(group)
        occupied = sum(not b.released and b.group_id == group for b in self.g.bindings.values())
        return {
            "managed": self.rules.get(group, {}).get("managed", False),
            "capacity": capacity,
            "occupied": occupied,
            "range": f"01–{capacity:02d}" if capacity else "暂无工位",
        }

    def remember_capacity(self, group):
        self.rules.setdefault(group, {"managed": False})["high_water"] = self.capacity(group)

    def validate(self, route, binding):
        # Existing bindings (including anonymous/custom workers) survive enabling strict rules.
        if binding or not self.rules.get(route.group, {}).get("managed"):
            return
        if not route.worker or not route.worker.isascii() or not route.worker.isdigit():
            raise GatewayError("INVALID_WORKER", "本组使用受管工位，请从管理页面复制有效链接", 422)
        number = int(route.worker)
        if not 1 <= number <= self.capacity(route.group) or route.worker != f"{number:02d}":
            raise GatewayError("INVALID_WORKER", "工位不在本组允许范围，请从管理页面复制", 422)

    def route(self, binding):
        return Route(
            self.g.config.socks_user, binding.session, binding.worker, binding.country, binding.group_id
        )

    def free_worker(self, group):
        self.remember_capacity(group)
        occupied = {b.worker for b in self.g.bindings.values() if b.group_id == group and not b.released}
        free = [f"{i:02d}" for i in range(1, self.capacity(group) + 1) if f"{i:02d}" not in occupied]
        if not free:
            raise GatewayError("NO_PENDING_LINKS", "无待用链接，请点击 IP 行内 SOCKS5／HTTP 复制对应链接")
        return random.choice(free)

    def ordinary(self, group):
        if (
            not self.g._fresh(None, group)
            or sum(self.g._fresh_eligible(n) for n in self.g.nodes.values()) <= self.g.settings.min_spare_ips
            or sum(not b.released for b in self.g.bindings.values()) >= self.g.active_limit()
        ):
            raise GatewayError("NO_PENDING_LINKS", "无待用链接，请点击 IP 行内 SOCKS5／HTTP 复制对应链接")
        pending = self.pending.get(group)
        if pending:
            route = Route(**pending)
            if (
                not self.g.binding_for(route.slot)
                and not self.g.retired.get(self.g._retired_key(route), 0) > time.time()
            ):
                return route
        route = Route(self.g.config.socks_user, uid()[:12], self.free_worker(group), group=group)
        self.pending[group] = asdict(route)
        return route

    def consume(self, route):
        if self.pending.get(route.group) == asdict(route):
            self.pending.pop(route.group, None)

    def reserve(self, node_id):
        g = self.g
        node = g.nodes.get(node_id)
        if not node:
            raise GatewayError("NOT_FOUND", "IP 不存在", 404)
        binding = g.bindings.get(node.binding_id)
        if binding and not binding.released and binding.node_id == node.id:
            if node.state not in {IPState.RESERVED, IPState.IN_USE}:
                raise GatewayError("NODE_BUSY", "当前出口不可用，请核对绑定状态")
            return self.route(binding), False
        if not g._fresh_eligible(node):
            raise GatewayError("NODE_NOT_ELIGIBLE", "只能预留已通过准入的待分配 IP，请刷新资源池")
        active = sum(not b.released for b in g.bindings.values())
        spare = sum(g._fresh_eligible(n) for n in g.nodes.values())
        if active >= g.active_limit() or spare <= g.settings.min_spare_ips:
            raise GatewayError("BINDING_LIMIT_REACHED", "可用绑定或热备不足，无法预留此 IP")
        pending = self.pending.get(node.group_id)
        route = Route(**pending) if pending else None
        if not route or g.binding_for(route.slot) or g.retired.get(g._retired_key(route), 0) > time.time():
            route = Route(
                g.config.socks_user, uid()[:12], self.free_worker(node.group_id), group=node.group_id
            )
        self.consume(route)
        binding = Binding(
            uid(),
            route.slot,
            route.session,
            route.worker,
            route.country,
            node.id,
            group_id=node.group_id,
            policy_version=g.quality.groups[node.group_id]["version"],
            reserved=True,
        )
        g.bindings[binding.id] = binding
        node.state, node.used, node.binding_id, node.generation = (
            IPState.RESERVED,
            False,
            binding.id,
            binding.generation,
        )
        node.reason = "已预留·待连接；取消预留不消耗更换额度"
        g.event("reservation", "已预留指定 IP", binding_id=binding.id, ip=node.ip, group_id=node.group_id)
        return route, True

    async def issue(self, group="default", node_id=None, key=""):
        g = self.g
        async with g.changed:
            g._writable()
            if node_id:
                node = g.nodes.get(node_id)
                if not node:
                    raise GatewayError("NOT_FOUND", "IP 不存在", 404)
                group = node.group_id
            if not g.quality.groups.get(group, {}).get("enabled"):
                raise GatewayError("GROUP_DISABLED", "分组不存在或已暂停", 403)
            fingerprint = f"{group}:{node_id or 'ordinary'}"
            old = self.receipts.get(key) if key else None
            if old and old["fingerprint"] != fingerprint:
                raise GatewayError("IDEMPOTENCY_CONFLICT", "幂等键已用于其他复制请求")
            if old:
                return Route(**old["route"]), False
            route, reserved = self.reserve(node_id) if node_id else (self.ordinary(group), False)
            if key:
                self.receipts[key] = {"fingerprint": fingerprint, "route": asdict(route)}
                while len(self.receipts) > 1000:
                    self.receipts.pop(next(iter(self.receipts)))
            await g._persist()
            g.changed.notify_all()
            return route, reserved

    async def save(self, group, managed):
        async with self.g.changed:
            self.g._writable()
            if group not in self.g.quality.groups:
                raise GatewayError("NOT_FOUND", "分组不存在", 404)
            self.remember_capacity(group)
            self.rules[group]["managed"] = managed
            await self.g._persist()
            return self.public(group)
