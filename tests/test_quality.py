import asyncio
import time
from dataclasses import replace

import pytest

from rapip.gateway import Gateway
from rapip.models import GatewayError, IPState, Route
from rapip.quality import QualityPolicy, evaluate
from rapip.storage import Storage

POLICY = {"samples": 1, "min_success": 1, "max_latency_ms": 1500}


async def create_group(gateway, id="register", count=2, **extra):
    ids = [n.id for n in gateway.nodes.values() if n.group_id == "default" and not n.used][:count]
    await gateway.quality.save_group(id, {"name": id, "policy": POLICY, **extra}, ids, create=True)
    return ids


@pytest.fixture
async def network(gateway, monkeypatch):
    async def check(node, policy):
        return {
            "complete": True,
            "successes": policy.samples,
            "samples": policy.samples,
            "max_latency_ms": 100,
            "exits": [node.ip],
            "exit_match": True,
        }

    monkeypatch.setattr(gateway.quality, "network_check", check)
    return check


def test_group_username_and_namespace():
    a = Route.parse("admin-country-US-group-register-worker-01-session-first")
    b = Route.parse("admin_group_collect_worker_01_session_first")
    assert a.group == "register" and a.country == "US" and a.slot != b.slot
    assert Route.parse("admin-worker-01-session-first").slot == "owner:worker:01"
    assert (
        Route.parse("admin-group-register-worker-01-session-first").slot
        != Route.parse("admin-session-first").slot
    )
    with pytest.raises(GatewayError):
        Route.parse("admin-group-register-group-collect-session-a")


async def test_only_selected_members_and_group_isolation(gateway, network):
    ids = await create_group(gateway)
    assert all(gateway.nodes[id].state == IPState.QUARANTINED for id in ids)
    result = await gateway.quality.run("register")
    assert result["summary"] == {"PASS": 2} and not gateway.bindings
    _, node, _ = await gateway.acquire(Route.parse("admin-group-register-worker-01-session-one"))
    assert node.id in ids
    _, default, _ = await gateway.acquire(Route.parse("admin-worker-01-session-one"))
    assert default.id not in ids
    assert len(gateway.bindings) == 2 and gateway.test_provider.calls == 0


async def test_no_cross_group_fallback(gateway, network):
    await create_group(gateway, count=0)
    gateway.workers.rules["register"]["managed"] = (
        False  # exercise pool guard independently of worker validation
    )
    with pytest.raises(GatewayError, match="没有符合|资源不足"):
        await gateway.acquire(Route.parse("admin-group-register-session-empty"))
    assert gateway.stats()["fresh_count"] == 6


async def test_member_uniqueness_and_active_move_rejected(gateway, network):
    ids = await create_group(gateway)
    await create_group(gateway, id="collect", count=0)
    with pytest.raises(GatewayError):
        await gateway.quality.members("collect", ids)
    await gateway.quality.run("register")
    _, node, _ = await gateway.acquire(Route.parse("admin-group-register-worker-01-session-first"))
    with pytest.raises(GatewayError):
        await gateway.quality.members("register", [node.id], remove=True)


def test_unknown_is_not_failure_or_false_flag():
    network = {"complete": True, "successes": 3, "max_latency_ms": 100, "exit_match": True}
    p = QualityPolicy(reject_flags=["hosting"], max_abuse_score=10)
    assert (
        evaluate(p, {"network": network, "ipinfo": {"flags": {}}, "abuseipdb": {"score": 100}})["state"]
        == "UNKNOWN"
    )
    assert evaluate(QualityPolicy(), {"network": network})["state"] == "PASS"
    assert evaluate(QualityPolicy(max_latency_ms=50), {"network": network})["state"] == "FAIL"
    assert evaluate(QualityPolicy(require_ipinfo=True), {"network": network})["state"] == "UNKNOWN"


async def test_missing_provider_never_replaces(gateway, network):
    await create_group(gateway, count=1, auto_replace=True, policy={**POLICY, "max_abuse_score": 10})
    result = await gateway.quality.run("register")
    assert result["summary"] == {"UNKNOWN": 1}
    assert not gateway.replacements and gateway.test_provider.calls == 0


async def test_budget_exact_and_replacement_inherits_slot(gateway, monkeypatch):
    async def slow(node, policy):
        return {"complete": True, "successes": 1, "exit_match": True, "max_latency_ms": 2000}

    monkeypatch.setattr(gateway.quality, "network_check", slow)
    ids = await create_group(
        gateway, count=1, auto_replace=True, daily_limit=1, slot_limit=3, cooldown_seconds=0
    )
    old = gateway.nodes[ids[0]]
    await gateway.quality.run("register")
    job = next(iter(gateway.replacements.values()))
    assert job.cause == "quality"
    await gateway.process_replacement(job.id)
    assert job.state == "COMPLETED" and gateway.test_provider.calls == 1
    new = next(n for n in gateway.nodes.values() if n.group_id == "register" and n.id != old.id)
    assert new.slot_id == old.slot_id and new.quality["state"] == "FAIL"
    assert new.state == IPState.QUARANTINED
    assert gateway.quality.slots[new.slot_id]["streak"] == 1
    await gateway.quality.run("register")
    assert gateway.test_provider.calls == 1 and len(gateway.replacements) == 1
    assert "预算" in new.reason


async def test_preview_does_not_change_membership_or_admission(gateway, network):
    nodes = list(gateway.nodes.values())[:2]
    before = [(n.group_id, n.state, dict(n.quality)) for n in nodes]
    r = await gateway.quality.run("default", [n.id for n in nodes], POLICY, preview=True)
    assert r["summary"] == {"PASS": 2}
    assert before == [(n.group_id, n.state, n.quality) for n in nodes]
    assert not gateway.replacements and not gateway.bindings


async def test_stale_policy_result_not_applied(gateway, monkeypatch):
    started, finish = asyncio.Event(), asyncio.Event()

    async def delayed(node, policy):
        started.set()
        await finish.wait()
        return {"complete": True, "successes": 1, "max_latency_ms": 100, "exit_match": True}

    monkeypatch.setattr(gateway.quality, "network_check", delayed)
    ids = await create_group(gateway, count=1)
    task = asyncio.create_task(gateway.quality.run("register"))
    await started.wait()
    await gateway.quality.save_group("register", {"policy": {**POLICY, "max_latency_ms": 60}})
    finish.set()
    await task
    assert gateway.nodes[ids[0]].state == IPState.QUARANTINED
    assert not gateway.nodes[ids[0]].quality


async def test_expired_admission_and_pause_never_leak_to_default(gateway, network):
    ids = await create_group(gateway, count=1)
    await gateway.quality.run("register")
    node = gateway.nodes[ids[0]]
    node.quality["expires_at"] = time.time() - 1
    assert not gateway._fresh_eligible(node)
    assert gateway.stats(group_id="register")["countries"]["US"]["fresh"] == 0
    assert gateway._fresh(None).group_id == "default"


async def test_restore_preserves_group_budget_and_source_usage(gateway, network, tmp_path):
    ids = await create_group(gateway, count=1)
    await gateway.quality.run("register")
    gateway.quality.slots[gateway.nodes[ids[0]].slot_id]["streak"] = 3
    gateway.quality.source_usage["2026-11-05:ipinfo"] = 5
    await gateway._persist()
    snapshot = await gateway.storage.load()
    store = Storage(tmp_path / "copy")
    await store.open()
    await store.save(snapshot)
    await store.close()
    instance = Gateway(
        replace(gateway.config, data_dir=tmp_path / "copy"), gateway.provider_factory, gateway.probe
    )
    await instance.start(background=False, sync=False)
    try:
        assert instance.nodes[ids[0]].group_id == "register"
        assert instance.quality.source_usage["2026-11-05:ipinfo"] == 5
        assert instance.quality.slots[instance.nodes[ids[0]].slot_id]["streak"] == 3
    finally:
        await instance.stop()


async def test_concurrent_group_failures_cannot_exceed_daily_limit(gateway, monkeypatch):
    async def slow(node, policy):
        return {"complete": True, "successes": 1, "exit_match": True, "max_latency_ms": 2000}

    monkeypatch.setattr(gateway.quality, "network_check", slow)
    await create_group(gateway, count=2, auto_replace=True, daily_limit=1, cooldown_seconds=0)
    await gateway.quality.run("register")
    jobs = list(gateway.replacements.values())
    assert len(jobs) == 2
    await asyncio.gather(*(gateway.process_replacement(j.id) for j in jobs))
    assert gateway.test_provider.calls == 1
    assert sum(j.state == "BUDGET_BLOCKED" for j in jobs) == 1


async def test_explicit_read_only_quality_check_cannot_replace(gateway, monkeypatch):
    async def slow(node, policy):
        return {"complete": True, "successes": 1, "exit_match": True, "max_latency_ms": 2000}

    monkeypatch.setattr(gateway.quality, "network_check", slow)
    await create_group(gateway, count=1, auto_replace=True, cooldown_seconds=0)
    result = await gateway.quality.run("register", allow_replace=False)
    assert result["summary"] == {"FAIL": 1} and not gateway.replacements


async def test_quality_rotation_remains_in_original_group(gateway, network):
    ids = await create_group(gateway, count=2)
    await gateway.quality.run("register")
    binding, first, _ = await gateway.acquire(Route.parse("admin-group-register-worker-01-session-a"))
    await gateway.rotate(binding.id, mode="hard")
    assert gateway.nodes[binding.node_id].id in ids
    assert gateway.nodes[binding.node_id].id != first.id
    with pytest.raises(GatewayError):
        await gateway.rotate(binding.id, mode="hard")


async def test_group_api_idempotency_and_csrf(client, gateway, network):
    headers = {"Authorization": "Bearer test-api-token", "Idempotency-Key": "create-group-001"}
    body = {
        "group_id": "collect",
        "name": "采集组",
        "node_ids": [next(iter(gateway.nodes))],
        "policy": POLICY,
    }
    response = await client.post("/api/v1/groups", headers=headers, json=body)
    assert response.status_code == 202
    await asyncio.sleep(0.02)
    again = await client.post("/api/v1/groups", headers=headers, json=body)
    assert again.json()["id"] == response.json()["id"]
    assert gateway.operations[response.json()["id"]].state == "COMPLETED"
    assert gateway.operations[response.json()["id"]].group_id == "collect"
    login = await client.post(
        "/api/v1/auth/login", json={"username": "admin", "password": "test-admin-password"}
    )
    assert login.status_code == 200
    blocked = await client.post(
        "/api/v1/groups/collect/check", headers={"Idempotency-Key": "quality-test-001"}, json={}
    )
    assert blocked.status_code == 403
    connection = await client.post(
        "/api/v1/connection-config", headers=headers, json={"group_id": "collect", "session_id": "first"}
    )
    assert "admin-group-collect-worker-01-session-first" in connection.json()["username"]


async def test_cached_evidence_does_not_extend_its_original_expiry(gateway, network, monkeypatch):
    checked = time.time() - 3600 + 60

    async def cached(*args):
        return {"source": "ipinfo", "checked_at": checked, "asn": "AS123", "country": "US", "flags": {}}

    monkeypatch.setattr(gateway.quality, "fetch_source", cached)
    ids = await create_group(gateway, count=1, policy={**POLICY, "require_ipinfo": True, "ttl_hours": 1})
    await gateway.quality.run("register")
    assert gateway.nodes[ids[0]].quality["expires_at"] <= checked + 3600 + 0.01


async def test_pausing_group_preserves_existing_binding_but_rejects_new(gateway, network):
    await create_group(gateway, count=2)
    await gateway.quality.run("register")
    route = Route.parse("admin-group-register-worker-01-session-a")
    original = await gateway.acquire(route)
    await gateway.quality.save_group("register", {"enabled": False})
    same = await gateway.acquire(route)
    assert same[0].id == original[0].id and same[1].id == original[1].id
    with pytest.raises(GatewayError) as error:
        await gateway.acquire(Route.parse("admin-group-register-worker-02-session-a"))
    assert error.value.code == "GROUP_DISABLED"
