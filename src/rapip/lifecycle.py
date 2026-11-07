"""Credential-free lifecycle records, committed alongside scheduling state."""

from __future__ import annotations

import time

from .models import uid


class Lifecycle:
    def __init__(self, gateway):
        self.g = gateway
        self.pending = []
        self.jobs = {}
        self.initialized = False
        self.cutoff = None
        self.related = {}
        self.keys = {}

    def add(self, kind, *, node=None, binding=None, route=None, **detail):
        item = {"id": uid(), "at": time.time(), "kind": kind}
        if node:
            item.update(
                ip=node.ip,
                country=node.country,
                node_id=node.id,
                slot_id=node.slot_id,
                account_id=node.account_id,
                group_id=node.group_id,
                generation=node.generation,
            )
            binding = binding or self.g.bindings.get(node.binding_id)
        if binding:
            item.update(binding_id=binding.id, worker=binding.worker, group_id=binding.group_id)
            if not node or node.generation == binding.generation:
                item["session"] = binding.session
                item["generation"] = binding.generation
        if route:
            item.update(worker=route.worker, session=route.session, group_id=route.group)
        item.update(detail)
        if node and kind in {"webshare", "admission", "draining"} and node.id in self.related:
            item.setdefault("chain_id", self.related[node.id])
        if binding and item.get("generation") and not item.get("chain_id"):
            if key := self.keys.get(f"{binding.id}:{item['generation']}"):
                item["chain_id"] = key
        self.pending.append(item)

    def follow(self, old, binding):
        self.related[old.id] = self.key(binding.id, binding.generation)

    def inherit(self, old, fresh):
        if chain := self.related.get(old.id):
            self.related[fresh.id] = chain

    def key(self, binding_id, generation):
        generation_key = f"{binding_id}:{generation or 1}"
        return self.keys.get(generation_key, f"binding:{generation_key}")

    def load(self, initialized):
        self.initialized = True
        for job in self.g.replacements.values():
            self.jobs[job.id] = self.signature(job)
        if initialized:
            return
        # Existing event records only supply facts that were actually recorded.
        for event in self.g.events:
            kind = {
                "rotate": "switched",
                "binding": "assigned",
                "reservation": "reserved",
                "release": "released",
            }.get(event.get("kind"))
            if not kind:
                continue
            fields = (
                "at",
                "binding_id",
                "ip",
                "old_ip",
                "group_id",
                "session",
                "old_session",
                "generation",
                "source",
            )
            self.add(kind, **{key: event[key] for key in fields if key in event}, historical=True)
        for binding in self.g.bindings.values():
            if not binding.released:
                self.add(
                    "baseline",
                    binding=binding,
                    node=self.g.nodes.get(binding.node_id),
                    status="RESERVED" if binding.reserved else "EXISTING",
                )
        for job in self.g.replacements.values():
            if job.state not in {"COMPLETED", "FAILED"}:
                self.job(job, baseline=True)

    @staticmethod
    def signature(job):
        return job.state, job.attempts, job.provider_id, job.fallback_reason, job.error, job.new_ip

    def job(self, job, baseline=False, status=None, reason=None):
        node = self.g.nodes.get(job.node_id)
        pending = self.g.dynamic.active_for_job(job)
        self.add(
            "webshare",
            node=node,
            group_id=job.group_id,
            slot_id=job.slot_id,
            status=status or job.state,
            reason=reason or job.error or job.reason,
            job_id=job.id,
            supplier_id=job.provider_id,
            previous_supplier_id=job.failed_provider_id,
            new_ip=job.new_ip,
            new_country=next(
                (
                    n.country
                    for n in self.g.nodes.values()
                    if n.ip == job.new_ip and n.account_id == job.account_id
                ),
                "",
            ),
            cause=job.cause,
            selection=job.selection,
            preferred_selection=job.preferred_selection,
            fallback_reason=job.fallback_reason,
            attempt=job.attempts,
            chain_id=(pending or {}).get("chain_id") or (self.related.get(node.id) if node else None),
            baseline=baseline,
        )

    def collect_jobs(self):
        for job in self.g.replacements.values():
            signature = self.signature(job)
            if self.jobs.get(job.id) != signature:
                self.jobs[job.id] = signature
                self.job(job)
