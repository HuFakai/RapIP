import asyncio
import time

import pytest

from rapip.models import GatewayError, IPState, Route

HEADERS = {"Authorization": "Bearer test-api-token"}


async def prepare(gateway, monkeypatch):
    async def network(node, policy):
        return {
            "complete": True,
            "successes": policy.samples,
            "samples": policy.samples,
            "max_latency_ms": 100,
            "exits": [node.ip],
            "exit_match": True,
        }

    monkeypatch.setattr(gateway.quality, "network_check", network)
    node = next(iter(gateway.nodes.values()))
    await gateway.quality.save_group(
        "retained", {"name": "固定项目", "auto_replace": True}, [node.id], create=True
    )
    await gateway.quality.run("retained", [node.id], allow_replace=False)
    binding, node, _ = await gateway.acquire(Route("admin", "permanent", "01", group="retained"))
    node.quality["expires_at"] = time.time() - 1
    return binding, node


@pytest.mark.parametrize("outcome", ["PASS", "FAIL", "UNKNOWN"])
async def test_refresh_never_changes_binding_or_replaces_bound_ip(gateway, monkeypatch, outcome):
    binding, node = await prepare(gateway, monkeypatch)
    before = (binding.id, binding.session, binding.generation, binding.node_id, node.ip, node.state)
    if outcome != "PASS":

        async def evidence(n, policy):
            if outcome == "UNKNOWN":
                return {"network": {"error": "检测服务暂不可用"}}
            return {
                "network": {
                    "complete": True,
                    "successes": 0,
                    "samples": policy.samples,
                    "exits": [n.ip],
                    "exit_match": True,
                }
            }

        monkeypatch.setattr(gateway.quality, "evidence", evidence)
    result = await gateway.quality.refresh_bound(node.id)
    assert result["quality"]["state"] == outcome
    assert result["binding_preserved"] and not result["replacement_submitted"]
    assert (binding.id, binding.session, binding.generation, binding.node_id, node.ip, node.state) == before
    assert node.state == IPState.IN_USE
    same, same_node, _ = await gateway.acquire(Route("admin", "permanent", "01", group="retained"))
    assert same.id == binding.id and same_node.id == node.id
    assert not gateway.replacements and gateway.test_provider.calls == 0
    saved = await gateway.storage.load()
    assert saved["nodes"][node.id]["quality"]["state"] == outcome
    assert node.id not in gateway.quality.busy


async def test_automatic_expiry_refresh_respects_switch_and_avoids_duplicate_work(gateway, monkeypatch):
    _, node = await prepare(gateway, monkeypatch)
    gateway.config.probe_enabled = False
    await gateway.quality.due()
    assert gateway.quality.state(node) == "EXPIRED"
    gateway.config.probe_enabled = True
    await gateway.quality.due()
    await gateway.quality.due()
    for _ in range(100):
        if gateway.quality.state(node) == "PASS":
            break
        await asyncio.sleep(0.01)
    assert gateway.quality.state(node) == "PASS"
    async with gateway.storage.db.execute(
        "SELECT count(*) FROM lifecycle WHERE kind='quality_refreshed'"
    ) as c:
        assert (await c.fetchone())[0] == 1
    assert gateway.test_provider.calls == 0


async def test_mid_refresh_policy_change_is_discarded_and_concurrent_refresh_rejected(gateway, monkeypatch):
    _, node = await prepare(gateway, monkeypatch)
    entered, finish = asyncio.Event(), asyncio.Event()
    original = gateway.quality.evidence

    async def slow(n, policy):
        entered.set()
        await finish.wait()
        return await original(n, policy)

    monkeypatch.setattr(gateway.quality, "evidence", slow)
    first = asyncio.create_task(gateway.quality.refresh_bound(node.id))
    await entered.wait()
    with pytest.raises(GatewayError, match="正在检测"):
        await gateway.quality.refresh_bound(node.id)
    gateway.quality.groups["retained"]["version"] += 1
    finish.set()
    with pytest.raises(GatewayError, match="已变化"):
        await first
    assert gateway.quality.state(node) == "EXPIRED"
    assert not gateway.replacements and gateway.test_provider.calls == 0
    assert not gateway.quality.bound_refreshing


async def test_bound_refresh_endpoint_and_clear_expiry_metadata(client, gateway, monkeypatch):
    binding, node = await prepare(gateway, monkeypatch)
    path = f"/api/v1/nodes/{node.id}/quality-refresh"
    assert (await client.post(path)).status_code == 401
    row = (await client.get("/api/v1/nodes?q=" + node.ip, headers=HEADERS)).json()["items"][0]
    assert row["quality"]["state"] == "EXPIRED"
    assert row["quality"]["binding_preserved"] and row["quality"]["auto_refresh"]
    assert "有效期" in row["quality"]["expiry_reason"]
    response = await client.post(path, headers={**HEADERS, "Idempotency-Key": "bound-refresh-test"}, json={})
    assert response.status_code == 202
    op = response.json()
    for _ in range(100):
        op = (await client.get("/api/v1/operations/" + op["id"], headers=HEADERS)).json()
        if op["state"] in ("COMPLETED", "FAILED"):
            break
        await asyncio.sleep(0.01)
    assert op["state"] == "COMPLETED" and op["result"]["quality"]["state"] == "PASS"
    assert binding.node_id == node.id and not gateway.replacements


async def test_onboarding_uses_real_success_records_not_merely_issued_links(client, gateway):
    assert (await client.get("/api/v1/onboarding")).status_code == 401
    first = (await client.get("/api/v1/onboarding", headers=HEADERS)).json()
    assert first["accounts"] == 1 and first["total_ips"] == 6
    assert not first["verified_connection"]
    binding, node, _ = await gateway.acquire(Route("admin", "unused", "01"))
    assert not (await client.get("/api/v1/onboarding", headers=HEADERS)).json()["verified_connection"]
    gateway.lifecycle.add("connected", node=node, binding=binding)
    async with gateway.changed:
        await gateway._persist()
    assert (await client.get("/api/v1/onboarding", headers=HEADERS)).json()["verified_connection"]
    assert not gateway.replacements and gateway.test_provider.calls == 0


async def test_near_expiry_source_cache_does_not_schedule_refresh_every_tick(gateway, monkeypatch):
    _, node = await prepare(gateway, monkeypatch)
    calls = 0

    async def cached(n, policy):
        nonlocal calls
        calls += 1
        return {
            "network": {
                "complete": True,
                "successes": policy.samples,
                "samples": policy.samples,
                "max_latency_ms": 100,
                "exits": [n.ip],
                "exit_match": True,
            },
            "ipinfo": {"checked_at": time.time() - policy.ttl_hours * 3600 + 60},
        }

    monkeypatch.setattr(gateway.quality, "evidence", cached)
    await gateway.quality.refresh_bound(node.id)
    assert node.quality["expires_at"] < time.time() + 120
    await gateway.quality.due()
    await gateway.quality.due()
    await asyncio.sleep(0.02)
    assert calls == 1 and gateway.test_provider.calls == 0


async def test_empty_onboarding_has_no_false_completed_connection(client, gateway):
    gateway.accounts, gateway.nodes = {}, {}
    response = await client.get("/api/v1/onboarding", headers=HEADERS)
    assert response.status_code == 200
    data = response.json()
    assert data["accounts"] == data["available_ips"] == data["bound_ips"] == data["total_ips"] == 0
    assert not data["verified_connection"]
