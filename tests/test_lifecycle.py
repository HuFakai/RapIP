from urllib.parse import unquote, urlsplit

import pytest

from rapip.models import IPState, Route
from rapip.provider import ProviderError
from rapip.replenishment import NO_STOCK

HEADERS = {"Authorization": "Bearer test-api-token"}


async def test_trace_separates_pool_switch_and_supplier_refill(client, gateway):
    response = await client.post("/api/v1/proxy-links", json={}, headers=HEADERS)
    data = response.json()
    assert unquote(urlsplit(data["links"]["socks5_named"]).fragment) == data["remarks"]
    assert data["remarks"].endswith("-待分配")
    assert not urlsplit(data["links"]["socks5"]).fragment
    route = Route.parse(data["username"])
    binding, old, _ = await gateway.acquire(route)

    current = await client.get(f"/api/v1/bindings/{binding.id}/proxy-link", headers=HEADERS)
    assert current.json()["remarks"].endswith("-" + old.ip)
    _, assigned, _ = await gateway.acquire(Route(route.user, "second", route.worker))
    assert assigned.id != old.id and gateway.test_provider.calls == 0
    job = next(iter(gateway.replacements.values()))
    assert job.selection == {"type": "any", "count": 1}
    await gateway.process_replacement(job.id)
    assert job.state == "COMPLETED" and job.new_ip not in {old.ip, assigned.ip}
    assert binding.node_id == assigned.id

    response = await client.get(f"/api/v1/lifecycle?binding_id={binding.id}", headers=HEADERS)
    rows = response.json()["items"]
    assert any(r["kind"] == "issued" and r["session"] == route.session for r in rows)
    event = next(r for r in rows if r["kind"] == "switched")
    assert (event["old_ip"], event["ip"], event["source"]) == (old.ip, assigned.ip, "client_session")
    assert "test-socks-password" not in response.text
    rows = (await client.get(f"/api/v1/lifecycle?view=ips&slot_id={old.slot_id}", headers=HEADERS)).json()[
        "items"
    ]
    assert any(r["kind"] == "webshare" and r["new_ip"] == job.new_ip for r in rows)
    assert any(
        r["kind"] == "admission" and r["ip"] == job.new_ip and r["status"] == IPState.FRESH for r in rows
    )
    assert (await client.get("/api/v1/lifecycle")).status_code == 401


@pytest.mark.parametrize("fallback", [True, False])
async def test_group_stock_rejection_falls_back_only_when_enabled(client, gateway, monkeypatch, fallback):
    async def options():
        return {"available_ip_ranges_24": {"203.0.113.0/24": 2}}

    monkeypatch.setattr(gateway.test_provider, "replacement_options", options, raising=False)
    res = await client.patch(
        "/api/v1/groups/default/replenishment",
        json={"mode": "ip_range", "ranges": ["203.0.113.0/24"], "fallback": fallback},
        headers=HEADERS,
    )
    assert res.status_code == 200 and gateway.test_provider.calls == 0
    node = next(iter(gateway.nodes.values()))
    async with gateway.changed:
        gateway._retire(node, "test")
        await gateway._persist()
    job = next(iter(gateway.replacements.values()))
    calls = []

    async def preview(ip, selection, dry_run=False):
        calls.append((selection, dry_run))
        return {"state": "failed", "error_code": NO_STOCK}

    monkeypatch.setattr(gateway.test_provider, "replace_selected", preview, raising=False)
    await gateway.process_replacement(job.id)
    assert calls == [({"type": "ip_range", "ip_ranges": ["203.0.113.0/24"], "count": 1}, True)]
    assert gateway.test_provider.calls == int(fallback)
    assert job.state == ("COMPLETED" if fallback else "FAILED")
    assert gateway.counters[node.account_id]["submitted"] == int(fallback)
    if fallback:
        assert job.selection["type"] == "any" and job.preferred_selection["type"] == "ip_range"
        assert job.fallback_reason == NO_STOCK


async def test_uncertain_real_submission_never_attempts_random(gateway, monkeypatch):
    gateway.replenishment["default"] = {"mode": "country", "country": "US"}
    node = next(iter(gateway.nodes.values()))
    async with gateway.changed:
        gateway._retire(node, "test")
        await gateway._persist()
    job = next(iter(gateway.replacements.values()))

    async def replace(ip, selection, dry_run=False):
        if dry_run:
            return {"state": "validated", "proxies_added": 1, "proxies_removed": 1}
        raise ProviderError(0, uncertain=True)

    monkeypatch.setattr(gateway.test_provider, "replace_selected", replace, raising=False)
    await gateway.process_replacement(job.id)
    assert job.state == "UNCERTAIN" and not job.fallback_reason
    assert job.selection["type"] == "country" and gateway.test_provider.calls == 0


@pytest.mark.parametrize("confirmed", [True, False])
async def test_accepted_job_only_falls_back_after_confirmed_zero_effect(gateway, monkeypatch, confirmed):
    gateway.replenishment["default"] = {"mode": "country", "country": "US"}
    node = next(iter(gateway.nodes.values()))
    async with gateway.changed:
        gateway._retire(node, "test")
        await gateway._persist()
    job = next(iter(gateway.replacements.values()))
    provider = gateway.test_provider
    original_lookup = provider.replacement

    async def replace(ip, selection, dry_run=False):
        if dry_run:
            return {"state": "validated", "proxies_added": 1, "proxies_removed": 1}
        return {"id": "targeted-123"}

    async def lookup(job_id):
        if job_id != "targeted-123":
            return await original_lookup(job_id)
        return {
            "state": "failed",
            "error_code": NO_STOCK,
            **({"proxies_added": 0, "proxies_removed": 0} if confirmed else {}),
        }

    monkeypatch.setattr(provider, "replace_selected", replace, raising=False)
    monkeypatch.setattr(provider, "replacement", lookup)
    await gateway.process_replacement(job.id)
    await gateway.process_replacement(job.id)
    assert job.state == ("COMPLETED" if confirmed else "FAILED")
    assert provider.calls == int(confirmed)
    assert gateway.counters[node.account_id]["submitted"] == 1 + int(confirmed)
