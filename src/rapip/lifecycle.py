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

    def add(self, kind, *, node=None, binding=None, route=None, **detail):
        item = {"id": uid(), "at": time.time(), "kind": kind}
        if node:
            item.update(
                ip=node.ip,
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
        self.pending.append(item)

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
            selection=job.selection,
            preferred_selection=job.preferred_selection,
            fallback_reason=job.fallback_reason,
            attempt=job.attempts,
            baseline=baseline,
        )

    def collect_jobs(self):
        for job in self.g.replacements.values():
            signature = self.signature(job)
            if self.jobs.get(job.id) != signature:
                self.jobs[job.id] = signature
                self.job(job)
