import time
from dataclasses import replace

import pytest

from rapip.gateway import Gateway
from rapip.models import GatewayError, IPState, Route
from rapip.rotation import ManualPreview, RotationPolicy, Selection
from rapip.storage import Storage


class Writer:
    closed = False

    def close(self):
        self.closed = True


def route(session="keep", worker="01", group="default"):
    return Route("admin", session, worker, group=group)


def allowance(gateway, remaining=100):
    gateway.supplier_usage["test"] = {
        "checked_at": time.time(),
        "plan_id": "1",
        "period_start": "2026-01-01T00:00:00+00:00",
        "replacement": {"remaining": remaining, "total": 100, "used": 100 - remaining},
    }


async def selected_provider(gateway):
    provider = gateway.test_provider
    provider.previews = []

    async def selected(ip, selection, dry_run=False):
        if dry_run:
            provider.previews.append((ip, selection))
            return {"id": "preview", "state": "validated", "proxies_added": 1, "proxies_removed": 1}
        return {"id": await provider.replace(ip, selection)}

    provider.replace_selected = selected
    return provider


async def test_disconnected_session_never_expires_or_consumes_quota(gateway):
    binding, old, _ = await gateway.acquire(route())
    binding.last_active = binding.last_detached = time.time() - 10 * 86400
    await gateway.maintain()
    current, node, _ = await gateway.acquire(route())
    assert current.id == binding.id and node.id == old.id
    assert not gateway.replacements and not gateway.retired
    assert gateway.public_binding(binding)["status"] == "RETAINED"


async def test_strict_fault_can_retry_same_ip_and_never_replaces(gateway):
    binding, node, _ = await gateway.acquire(route())
    for _ in range(3):
        assert not await gateway.connection_failure(node.id, "transport")
    assert binding.unavailable and not gateway.replacements
    _, again, _ = await gateway.acquire(route())
    assert again.id == node.id
    await gateway.connection_success(node.id)
    assert not binding.unavailable


async def test_external_removal_strict_mode_does_not_allocate_other_exit(gateway):
    binding, node, _ = await gateway.acquire(route())
    gateway.test_provider.rows = [r for r in gateway.test_provider.rows if r["proxy_address"] != node.ip]
    await gateway.sync_account("test")
    assert binding.unavailable
    with pytest.raises(GatewayError) as error:
        await gateway.acquire(route())
    assert error.value.code == "EXIT_UNAVAILABLE"
    assert not gateway.replacements


async def test_restart_keeps_original_ip_and_schedule_budget(gateway):
    allowance(gateway)
    await gateway.rotation.save("default", RotationPolicy(enabled=True, daily_limit=2, total_limit=3))
    binding, node, _ = await gateway.acquire(route())
    binding.rotation = {"next_at": 1, "status": "RUNNING"}
    await gateway._persist()
    directory = gateway.config.data_dir.parent / "rotation-recovery"
    config = replace(gateway.config, data_dir=directory)
    store = Storage(directory)
    await store.open()
    await store.save(await gateway.storage.load())
    await store.close()
    recovered = Gateway(config, provider_factory=gateway.provider_factory, probe=gateway.probe)
    await recovered.start(background=False)
    try:
        _, same, _ = await recovered.acquire(route())
        assert same.ip == node.ip and not recovered.replacements
        assert not recovered.bindings[binding.id].rotation
        assert recovered.rotation.policy("default")["total_limit"] == 3
        await recovered.rotation.tick()
        assert not recovered.replacements  # no catch-up until successful business connection
    finally:
        await recovered.stop()


async def test_timer_pauses_without_releasing_and_resumes_full_interval(gateway):
    allowance(gateway)
    await gateway.rotation.save(
        "default",
        RotationPolicy(
            enabled=True, interval_seconds=60, idle_pause_seconds=10, daily_limit=3, total_limit=3
        ),
    )
    binding, node, generation = await gateway.acquire(route())
    writer = Writer()
    tid = await gateway.attach(binding.id, node.id, generation, writer, writer)
    await gateway.detach(tid)
    binding.last_detached = time.time() - 20
    binding.rotation["next_at"] = time.time() - 1
    await gateway.rotation.tick()
    assert binding.rotation["status"] == "IDLE_PAUSED"
    assert not gateway.replacements and not binding.released
    tid = await gateway.attach(binding.id, node.id, generation, writer, writer)
    assert binding.rotation["next_at"] > time.time() + 55
    await gateway.rotation.tick()
    assert binding.node_id == node.id and not gateway.replacements
    await gateway.detach(tid)


async def test_scheduled_rotation_keeps_session_and_reserves_refill(gateway):
    allowance(gateway)
    await gateway.rotation.save(
        "default",
        RotationPolicy(
            enabled=True, interval_seconds=60, idle_pause_seconds=600, daily_limit=1, total_limit=1
        ),
    )
    binding, node, _ = await gateway.acquire(route())
    gateway.rotation.connected(binding)
    binding.rotation["next_at"] = time.time() - 1
    await gateway.rotation.tick()
    assert binding.session == "keep" and binding.node_id != node.id
    job = next(iter(gateway.replacements.values()))
    assert job.reservation_id and gateway.rotation.ledger[job.reservation_id]["state"] == "RESERVED"
    await gateway.process_replacement(job.id)
    assert job.state == "COMPLETED" and gateway.test_provider.calls == 1
    binding.rotation["next_at"] = time.time() - 1
    await gateway.rotation.tick()
    assert binding.rotation["status"] == "BUDGET_PAUSED"
    assert len(gateway.replacements) == 1


@pytest.mark.parametrize("remaining,daily,total", [(0, 3, 3), (100, 0, 3), (100, 3, 0)])
async def test_zero_budget_never_means_unlimited(gateway, remaining, daily, total):
    allowance(gateway, remaining)
    await gateway.rotation.save("default", RotationPolicy(enabled=True, daily_limit=daily, total_limit=total))
    binding, node, _ = await gateway.acquire(route())
    gateway.rotation.connected(binding)
    binding.rotation["next_at"] = 1
    await gateway.rotation.tick()
    assert binding.node_id == node.id and not gateway.replacements
    assert binding.rotation["status"] == "BUDGET_PAUSED"


async def test_unknown_supplier_balance_blocks_scheduled_replacement(gateway):
    gateway.supplier_usage.clear()
    await gateway.rotation.save("default", RotationPolicy(enabled=True, daily_limit=3, total_limit=3))
    binding, node, _ = await gateway.acquire(route())
    gateway.rotation.connected(binding)
    binding.rotation["next_at"] = 1
    await gateway.rotation.tick()
    assert binding.node_id == node.id and not gateway.replacements


async def test_group_disabled_and_no_spare_leave_binding_untouched(gateway):
    allowance(gateway)
    await gateway.rotation.save("default", RotationPolicy(enabled=True, daily_limit=3, total_limit=3))
    binding, node, _ = await gateway.acquire(route())
    gateway.rotation.connected(binding)
    binding.rotation["next_at"] = 1
    for other in gateway.nodes.values():
        if other.id != node.id:
            other.state = IPState.QUARANTINED
    await gateway.rotation.tick()
    assert binding.rotation["status"] == "WAITING_STOCK" and not gateway.replacements


async def test_manual_preview_does_not_change_resources_and_commit_rebinds(gateway):
    allowance(gateway)
    provider = await selected_provider(gateway)
    binding, node, _ = await gateway.acquire(route())
    body = ManualPreview(node_ids=[node.id], bound_action="replace")
    preview = await gateway.rotation.preview(body)
    assert node.state == IPState.IN_USE and provider.calls == 0
    committed = await gateway.rotation.commit(preview["id"])
    await gateway.rotation.tick()
    request = gateway.rotation.requests[committed["request_ids"][0]]
    job = gateway.replacements[request["job_id"]]
    assert binding.unavailable and job.rebind_id == binding.id
    await gateway.process_replacement(job.id)
    _, new, _ = await gateway.acquire(route())
    assert new.ip != node.ip and new.state == IPState.IN_USE and new.used
    assert new.binding_id == binding.id and binding.session == "keep"
    assert provider.calls == 1 and provider.previews


async def test_manual_wait_reserves_budget_and_cancel_is_safe(gateway):
    allowance(gateway)
    await selected_provider(gateway)
    binding, node, generation = await gateway.acquire(route())
    tid = await gateway.attach(binding.id, node.id, generation, Writer(), Writer())
    preview = await gateway.rotation.preview(ManualPreview(node_ids=[node.id], bound_action="wait"))
    result = await gateway.rotation.commit(preview["id"])
    rid = result["request_ids"][0]
    await gateway.rotation.tick()
    assert gateway.rotation.requests[rid]["state"] == "WAITING" and not gateway.replacements
    assert gateway.account_idle_error("test")
    await gateway.rotation.cancel(rid)
    await gateway.detach(tid)
    await gateway.rotation.tick()
    assert not gateway.replacements and not gateway.rotation.pending()


async def test_preview_stale_and_double_commit_cannot_replace_twice(gateway):
    allowance(gateway)
    await selected_provider(gateway)
    node = next(iter(gateway.nodes.values()))
    preview = await gateway.rotation.preview(ManualPreview(node_ids=[node.id]))
    node.membership_version += 1
    with pytest.raises(GatewayError):
        await gateway.rotation.commit(preview["id"])
    assert not gateway.rotation.pending()
    preview = await gateway.rotation.preview(ManualPreview(node_ids=[node.id]))
    await gateway.rotation.commit(preview["id"])
    with pytest.raises(GatewayError):
        await gateway.rotation.commit(preview["id"])
    assert len(gateway.rotation.pending()) == 1


async def test_batch_reservation_atomic_on_insufficient_budget(gateway):
    allowance(gateway, 1)
    await selected_provider(gateway)
    preview = await gateway.rotation.preview(
        ManualPreview(node_ids=[n.id for n in list(gateway.nodes.values())[:2]])
    )
    with pytest.raises(GatewayError):
        await gateway.rotation.commit(preview["id"])
    assert not gateway.rotation.pending() and not gateway.rotation.requests


async def test_pending_dials_enforce_limit_before_upstream_and_release(gateway):
    binding, node, _ = await gateway.acquire(route())
    gateway.settings.max_connections_per_binding = 1
    await gateway.reserve_dial(binding)
    with pytest.raises(GatewayError) as error:
        await gateway.reserve_dial(binding)
    assert error.value.code == "BINDING_CONNECTION_LIMIT" and "1/1" in error.value.message
    gateway.release_dial(binding.id)
    await gateway.reserve_dial(binding)
    gateway.release_dial(binding.id)
    assert not gateway.dialing and not gateway.replacements and not binding.released


async def test_rotation_api_auth_validation_and_idempotency(client, gateway):
    assert (await client.put("/api/v1/groups/default/rotation", json={})).status_code == 401
    headers = {"Authorization": "Bearer test-api-token", "Idempotency-Key": "rotation-api"}
    body = {"enabled": False, "fault_failover": False}
    r = await client.put("/api/v1/groups/default/rotation", json=body, headers=headers)
    assert r.status_code == 202
    assert (await client.put("/api/v1/groups/default/rotation", json=body, headers=headers)).json()[
        "id"
    ] == r.json()["id"]
    assert (
        await client.put("/api/v1/groups/default/rotation", json={"interval_seconds": 0}, headers=headers)
    ).status_code == 422
    assert (
        await client.patch("/api/v1/settings", json={"session_idle_timeout": 300}, headers=headers)
    ).status_code == 422


@pytest.mark.parametrize("selection", [{"type": "country"}, {"type": "asn"}, {"type": "ip_range"}])
def test_incomplete_selection_rejected(gateway, selection):
    with pytest.raises(GatewayError):
        Selection(**selection).supplier(next(iter(gateway.nodes.values())))


async def test_original_external_ip_reappears_restores_exclusive_ownership(gateway):
    binding, node, _ = await gateway.acquire(route())
    original = copy_row = dict(next(r for r in gateway.test_provider.rows if r["proxy_address"] == node.ip))
    gateway.test_provider.rows.remove(original)
    await gateway.sync_account("test")
    assert node.state == IPState.ARCHIVED and binding.unavailable
    gateway.test_provider.rows.append(copy_row)
    await gateway.sync_account("test")
    _, same, _ = await gateway.acquire(route())
    assert same.id == node.id and not binding.unavailable
    assert sum(n.ip == node.ip for n in gateway.nodes.values()) == 1


async def test_manual_quality_failure_never_admits_replacement_to_binding(gateway, monkeypatch):
    allowance(gateway)
    await selected_provider(gateway)
    ids = [n.id for n in list(gateway.nodes.values())[:2]]
    await gateway.quality.save_group(
        "quality", {"name": "quality", "policy": {"samples": 1, "min_success": 1}}, ids, create=True
    )

    async def check(node, policy):
        return {"complete": True, "successes": 1, "max_latency_ms": 100, "exit_match": True}

    monkeypatch.setattr(gateway.quality, "network_check", check)
    await gateway.quality.run("quality")
    binding, node, _ = await gateway.acquire(route(group="quality"))
    preview = await gateway.rotation.preview(ManualPreview(node_ids=[node.id], bound_action="replace"))
    await gateway.rotation.commit(preview["id"])
    await gateway.rotation.tick()

    async def fail(node, policy):
        return {"complete": True, "successes": 0, "max_latency_ms": 100, "exit_match": True}

    monkeypatch.setattr(gateway.quality, "network_check", fail)
    job = next(iter(gateway.replacements.values()))
    await gateway.process_replacement(job.id)
    assert job.state == "COMPLETED"
    fresh = gateway.nodes[binding.node_id]
    assert fresh.state == IPState.QUARANTINED and binding.unavailable
    with pytest.raises(GatewayError):
        await gateway.acquire(route(group="quality"))
    assert gateway.test_provider.calls == 1


async def test_uncertain_parameter_post_is_never_repeated(gateway):
    allowance(gateway)
    await selected_provider(gateway)
    node = next(iter(gateway.nodes.values()))
    preview = await gateway.rotation.preview(ManualPreview(node_ids=[node.id]))
    await gateway.rotation.commit(preview["id"])
    await gateway.rotation.tick()
    gateway.test_provider.uncertain = True
    job = next(iter(gateway.replacements.values()))
    await gateway.process_replacement(job.id)
    await gateway.process_replacement(job.id)
    assert job.state == "UNCERTAIN" and gateway.test_provider.calls == 1
    assert gateway.rotation.ledger[job.reservation_id]["state"] == "SUBMITTED"


async def test_save_and_reconnect_do_not_reset_authorized_total(gateway):
    allowance(gateway)
    policy = RotationPolicy(enabled=True, daily_limit=1, total_limit=1)
    await gateway.rotation.save("default", policy)
    binding, node, _ = await gateway.acquire(route())
    rid = gateway.rotation.reserve(node, "scheduled", binding.id)
    gateway.rotation.ledger[rid]["state"] = "SUBMITTED"
    epoch = gateway.rotation.policy("default")["epoch"]
    await gateway.rotation.save("default", policy)
    gateway.rotation.connected(binding)
    assert gateway.rotation.public("default")["total_used"] == 1
    assert gateway.rotation.policy("default")["epoch"] == epoch
    # New day refreshes daily only, never the full authorization.
    gateway.rotation.ledger[rid]["day"] = "2000-01-01"
    assert gateway.rotation.public("default")["today_used"] == 0
    with pytest.raises(GatewayError):
        gateway.rotation.reserve(node, "scheduled", binding.id)
    await gateway.rotation.save("default", policy, reset=True)
    assert gateway.rotation.public("default")["total_used"] == 0


async def test_changing_session_cancels_waiting_manual_identity(gateway):
    allowance(gateway)
    await selected_provider(gateway)
    binding, node, _ = await gateway.acquire(route())
    preview = await gateway.rotation.preview(ManualPreview(node_ids=[node.id], bound_action="wait"))
    result = await gateway.rotation.commit(preview["id"])
    await gateway.acquire(route(session="next"))
    await gateway.rotation.tick()
    request = gateway.rotation.requests[result["request_ids"][0]]
    assert request["state"] == "CANCELLED"
    assert gateway.rotation.ledger[request["reservation_id"]]["state"] == "CANCELLED"
    refill = next(iter(gateway.replacements.values()))
    assert [r["id"] for r in gateway.rotation.pending()] == [refill.reservation_id]
    assert binding.session == "next"


async def test_explicit_spare_limit_applies_alongside_binding_cap(gateway):
    gateway.settings.min_spare_ips = 5
    gateway.settings.max_active_bindings = 20
    await gateway.acquire(route())
    with pytest.raises(GatewayError):
        await gateway.acquire(route(worker="02"))
    assert gateway.stats()["fresh_count"] == 5


async def test_legacy_unsubmitted_idle_replacement_is_paused_on_migration(gateway):
    binding, node, _ = await gateway.acquire(route())
    async with gateway.changed:
        gateway._release(binding, "hard")
        gateway._retire(node, "绑定释放")
        await gateway._persist()
    snapshot = await gateway.storage.load()
    snapshot["schema"] = 2
    directory = gateway.config.data_dir.parent / "legacy-upgrade"
    store = Storage(directory)
    await store.open()
    await store.save(snapshot)
    await store.close()
    recovered = Gateway(
        replace(gateway.config, data_dir=directory),
        provider_factory=gateway.provider_factory,
        probe=gateway.probe,
    )
    await recovered.start(background=False, sync=False)
    try:
        job = next(iter(recovered.replacements.values()))
        assert job.state == "FAILED" and "旧版本" in job.error
        assert recovered.nodes[node.id].state == IPState.QUARANTINED
        assert gateway.test_provider.calls == 0
    finally:
        await recovered.stop()


async def test_recovery_cancels_uncommitted_orphan_but_preserves_waiting_manual(gateway):
    import copy

    from rapip.rotation import RotationManager

    allowance(gateway)
    await gateway.rotation.save("default", RotationPolicy(enabled=True, daily_limit=3, total_limit=3))
    nodes = list(gateway.nodes.values())
    orphan = gateway.rotation.reserve(nodes[0], "scheduled")
    waiting = gateway.rotation.reserve(nodes[1], "manual")
    gateway.rotation.requests["waiting"] = {"reservation_id": waiting, "state": "WAITING"}
    manager = RotationManager(gateway)
    manager.load(copy.deepcopy(gateway.rotation.snapshot()))
    assert manager.ledger[orphan]["state"] == "CANCELLED"
    assert manager.ledger[waiting]["state"] == "RESERVED"


async def test_scheduler_serves_oldest_due_binding_without_worker_starvation(gateway):
    allowance(gateway)
    await gateway.rotation.save("default", RotationPolicy(enabled=True, daily_limit=10, total_limit=10))
    a, old_a, _ = await gateway.acquire(route(worker="01"))
    b, old_b, _ = await gateway.acquire(route(worker="02"))
    gateway.rotation.connected(a)
    gateway.rotation.connected(b)
    a.rotation["next_at"] = time.time() - 10
    b.rotation["next_at"] = time.time() - 100
    await gateway.rotation.tick()
    assert a.node_id == old_a.id and b.node_id != old_b.id
