import asyncio
import time

import pytest
from test_quality import create_group

from rapip.models import GatewayError, IPState, Route
from rapip.provider import ProviderError

pytestmark = pytest.mark.usefixtures("quality_network")


@pytest.fixture
def quality_network(gateway, monkeypatch):
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


def route(session, worker="01", group="default"):
    return Route("admin", session, worker, group=group)


async def retire(gateway, node):
    async with gateway.changed:
        gateway._retire(node, "预算回归测试")
        await gateway._persist()
    return next(j for j in gateway.replacements.values() if j.node_id == node.id)


async def test_pool_switch_reserves_last_quota_and_keeps_binding_on_next_request(gateway):
    gateway.accounts["test"].replacement_budget = 1
    binding, old, _ = await gateway.acquire(route("first"))
    _, current, _ = await gateway.acquire(route("second"))
    spare = {n.id for n in gateway.nodes.values() if n.state == IPState.FRESH}
    with pytest.raises(GatewayError, match="账号本地预算不足"):
        await gateway.acquire(route("third"))
    assert binding.session == "second" and binding.node_id == current.id
    assert {n.id for n in gateway.nodes.values() if n.state == IPState.FRESH} == spare
    assert f"{binding.slot}:second" not in gateway.retired
    job = next(iter(gateway.replacements.values()))
    assert job.node_id == old.id and len(gateway.rotation.pending()) == 1
    await gateway.process_replacement(job.id)
    assert job.state == "COMPLETED" and gateway.test_provider.calls == 1
    assert not gateway.rotation.pending()


async def test_concurrent_workers_cannot_reserve_same_final_allowance(gateway):
    gateway.accounts["test"].replacement_budget = 1
    first, _, _ = await gateway.acquire(route("first", "01"))
    second, _, _ = await gateway.acquire(route("first", "02"))
    results = await asyncio.gather(
        gateway.acquire(route("next", "01")),
        gateway.acquire(route("next", "02")),
        return_exceptions=True,
    )
    assert sum(isinstance(r, GatewayError) for r in results) == 1
    assert sorted([first.generation, second.generation]) == [1, 2]
    assert len(gateway.rotation.pending()) == len(gateway.replacements) == 1
    assert gateway.test_provider.calls == 0


async def test_exhausted_group_total_preserves_pool_and_session(gateway):
    await create_group(gateway, count=3, round_limit=0, daily_limit=10)
    await gateway.quality.run("register")
    binding, old, _ = await gateway.acquire(route("first", group="register"))
    with pytest.raises(GatewayError, match="本次授权总额度"):
        await gateway.acquire(route("next", group="register"))
    assert binding.session == "first" and binding.node_id == old.id
    assert sum(n.state == IPState.FRESH and n.group_id == "register" for n in gateway.nodes.values()) == 2
    assert not gateway.replacements and not gateway.rotation.pending()


@pytest.mark.parametrize("unknown", [False, True])
async def test_every_refill_checks_vendor_balance_before_io(gateway, monkeypatch, unknown):
    node = next(iter(gateway.nodes.values()))
    job = await retire(gateway, node)
    gateway.supplier_usage["test"]["replacement"]["remaining"] = 0
    if unknown:
        gateway.supplier_usage["test"]["checked_at"] = time.time() - 901

    async def forbidden():
        pytest.fail("blocked job must not call even the supplier list API")

    monkeypatch.setattr(gateway.test_provider, "list_nodes", forbidden)
    await gateway.process_replacement(job.id)
    assert job.state == "BUDGET_BLOCKED" and job.attempts == 0
    assert gateway.test_provider.calls == 0
    assert not gateway.rotation.pending()
    assert not gateway.rotation.resume_blocked()
    assert job.state == "BUDGET_BLOCKED"


async def test_budget_wait_only_resumes_after_all_layers_allow(gateway):
    await create_group(gateway, count=1, auto_replace=False, round_limit=10)
    node = next(n for n in gateway.nodes.values() if n.group_id == "register")
    job = await retire(gateway, node)
    job.cause = "quality"
    gateway.accounts["test"].replacement_budget = 0
    await gateway.process_replacement(job.id)
    for _ in range(3):
        await gateway.quality.due()
        await gateway.rotation.tick()
        assert job.state == "BUDGET_BLOCKED"
    gateway.accounts["test"].replacement_budget = 20
    assert gateway.rotation.resume_blocked()  # reason changes to the disabled quality policy
    assert job.state == "BUDGET_BLOCKED" and "已关闭" in job.error
    gateway.quality.groups["register"]["auto_replace"] = True
    assert gateway.rotation.resume_blocked() and job.state == "QUEUED"
    assert gateway.test_provider.calls == 0


async def test_supplier_allowance_rechecked_after_preflight(gateway, monkeypatch):
    gateway.replenishment["default"] = {"mode": "country", "country": "US"}
    job = await retire(gateway, next(iter(gateway.nodes.values())))

    async def preview(*args, **kwargs):
        assert kwargs["dry_run"] is True
        gateway.supplier_usage["test"]["replacement"]["remaining"] = 0
        return {"state": "validated", "proxies_added": 1, "proxies_removed": 1}

    monkeypatch.setattr(gateway.test_provider, "replace_selected", preview, raising=False)
    await gateway.process_replacement(job.id)
    assert job.state == "BUDGET_BLOCKED" and job.attempts == 0
    assert gateway.counters["test"]["submitted"] == gateway.test_provider.calls == 0


async def test_preflight_resume_id_is_persisted_and_never_reposts_preview(gateway, monkeypatch):
    gateway.replenishment["default"] = {"mode": "country", "country": "US"}
    job = await retire(gateway, next(iter(gateway.nodes.values())))
    previews, polls = [], []

    async def preview(ip, selection, dry_run=False):
        if dry_run:
            previews.append(ip)
            return {"id": "dry-run-id", "state": "pending"}
        return {"id": await gateway.test_provider.replace(ip, "US")}

    async def lookup(pid):
        polls.append(pid)
        if len(polls) == 1:
            saved = await gateway.storage.load()
            assert saved["replacements"][job.id]["preflight_id"] == "dry-run-id"
            raise TimeoutError()
        return {
            "state": "validated" if pid == "dry-run-id" else "completed",
            "proxies_added": 1,
            "proxies_removed": 1,
        }

    monkeypatch.setattr(gateway.test_provider, "replace_selected", preview, raising=False)
    monkeypatch.setattr(gateway.test_provider, "replacement", lookup)
    await gateway.process_replacement(job.id)
    assert job.state == "QUEUED" and job.retry_at > time.time()
    assert job.attempts == gateway.test_provider.calls == 0
    await gateway.process_replacement(job.id)
    assert job.state == "COMPLETED" and len(previews) == 1
    assert polls[:2] == ["dry-run-id", "dry-run-id"]
    assert gateway.test_provider.calls == 1


async def test_preview_outage_stops_after_three_windows_without_random_fallback(gateway, monkeypatch):
    gateway.replenishment["default"] = {"mode": "country", "country": "US", "fallback": True}
    node = next(iter(gateway.nodes.values()))
    job = await retire(gateway, node)
    previews = []

    async def unavailable(*args, **kwargs):
        previews.append(kwargs["dry_run"])
        raise ProviderError(503)

    monkeypatch.setattr(gateway.test_provider, "replace_selected", unavailable, raising=False)
    for _ in range(5):
        await gateway.process_replacement(job.id)
    assert previews == [True] * 3
    assert job.state == "FAILED" and job.preflight_attempts == 3
    assert node.state == IPState.QUARANTINED and node.replacement_error
    assert not gateway.rotation.pending() and not job.fallback_reason
    assert job.attempts == gateway.test_provider.calls == 0


async def test_nodes_expose_budget_pause_and_hide_historical_worker(gateway, client):
    binding, old, _ = await gateway.acquire(route("first"))
    await gateway.acquire(route("next"))
    gateway.accounts["test"].replacement_budget = 0
    job = next(iter(gateway.replacements.values()))
    await gateway.process_replacement(job.id)
    response = await client.get("/api/v1/nodes", headers={"Authorization": "Bearer test-api-token"})
    row = next(r for r in response.json()["items"] if r["id"] == old.id)
    assert row["replacement_status"]["state"] == "BUDGET_BLOCKED"
    assert row["replacement_status"]["attempts"] == 0 and row["worker"] is None
    assert binding.session == "next"
    assert gateway.stats()["budget_blocked_count"] == 1


async def test_graceful_drain_keeps_refill_reservation_until_submission(gateway):
    class Writer:
        def close(self):
            pass

    gateway.accounts["test"].replacement_budget = 1
    binding, old, _ = await gateway.acquire(route("first"))
    gateway.tunnels["active"] = {"binding_id": binding.id, "node_id": old.id, "writer": Writer()}
    await gateway.acquire(route("next"))
    assert old.state == IPState.DRAINING and not gateway.replacements
    reservation = gateway.rotation.pending()[0]
    assert reservation["committed"]
    saved = await gateway.storage.load()
    gateway.rotation.load(saved["rotation"])
    assert gateway.rotation.pending()[0]["id"] == reservation["id"]
    gateway.tunnels.clear()
    await gateway.maintain()
    job = next(iter(gateway.replacements.values()))
    assert job.reservation_id == reservation["id"]
    await gateway.process_replacement(job.id)
    assert job.state == "COMPLETED" and gateway.test_provider.calls == 1


async def test_restart_after_third_preflight_window_does_not_start_fourth(gateway, monkeypatch):
    gateway.replenishment["default"] = {"mode": "country", "country": "US"}
    job = await retire(gateway, next(iter(gateway.nodes.values())))
    job.preflight_attempts, job.preflight_id = 3, "last-dry-run"

    async def forbidden(*args, **kwargs):
        pytest.fail("exhausted preview must not send another request")

    monkeypatch.setattr(gateway.test_provider, "replace_selected", forbidden, raising=False)
    await gateway.process_replacement(job.id)
    assert job.state == "FAILED" and job.attempts == gateway.test_provider.calls == 0
    assert not gateway.rotation.pending()
