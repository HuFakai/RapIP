from dataclasses import replace

import pytest

from rapip.gateway import Gateway
from rapip.models import GatewayError, IPState, Route
from rapip.ping0 import parse_page
from rapip.quality import QualityPolicy, evaluate
from rapip.storage import Storage


def route(session="one", worker="01"):
    return Route("admin", session, worker)


async def test_release_used_exit_can_be_reallocated_without_supplier_charge(gateway):
    binding, node, _ = await gateway.acquire(route())
    await gateway.release(binding.id, "hard")
    assert node.state == IPState.FRESH and not node.used and not node.binding_id
    assert not gateway.replacements and gateway.test_provider.calls == 0
    with pytest.raises(GatewayError):
        await gateway.acquire(route())
    _, again, _ = await gateway.acquire(route("next"))
    assert again.id == node.id
    await gateway.maintain()
    assert not gateway.replacements


async def test_graceful_release_returns_after_drain_and_restart(gateway, tmp_path):
    class Writer:
        def close(self):
            pass

    binding, node, _ = await gateway.acquire(route())
    tunnel = await gateway.attach(binding.id, node.id, binding.generation, Writer(), Writer())
    await gateway.release(binding.id, "graceful")
    assert node.state == IPState.DRAINING and node.id in gateway.returning
    assert not gateway.replacements
    store = Storage(tmp_path / "restored")
    await store.open()
    await store.save(await gateway.storage.load())
    await store.close()
    restored = Gateway(
        replace(gateway.config, data_dir=tmp_path / "restored"),
        provider_factory=gateway.provider_factory,
        probe=gateway.probe,
    )
    await restored.start(background=False, sync=False)
    try:
        assert restored.nodes[node.id].state == IPState.FRESH
        assert not restored.replacements and not restored.returning
    finally:
        await restored.stop()
    await gateway.detach(tunnel)
    await gateway.maintain()
    assert node.state == IPState.FRESH and not gateway.returning


async def test_echo_failure_falls_back_and_measures_successful_endpoint_only(gateway, monkeypatch):
    node = next(iter(gateway.nodes.values()))

    async def echo(node, host):
        return {
            "host": host,
            "ok": host != "api.ipify.org",
            "ip": node.ip,
            "elapsed_ms": 5000 if host == "api.ipify.org" else 100,
        }

    monkeypatch.setattr(gateway.quality, "echo", echo)
    evidence = await gateway.quality.network_check(node, QualityPolicy(samples=2, min_success=2))
    assert evidence["successes"] == 2 and evidence["max_latency_ms"] == 100
    assert len(evidence["attempts"]) == 4
    assert evaluate(QualityPolicy(samples=2, min_success=2), {"network": evidence})["state"] == "PASS"
    verdict = evaluate(
        QualityPolicy(), {"network": {"complete": True, "successes": 0, "exits": [], "exit_match": False}}
    )
    assert verdict["state"] == "UNKNOWN" and any("未取得出口回显" in r for r in verdict["reasons"])
    assert not any("不一致" in r for r in verdict["reasons"])


def test_ping0_validates_exit_and_distinguishes_challenge_from_bad_quality():
    challenge = b'<html><script src="https://challenges.cloudflare.com/turnstile/v0/api.js"></script></html>'
    assert parse_page(challenge, 200, "1.2.3.4")["site_access"] == "CHALLENGE"
    assert parse_page(b"rate", 429, "1.2.3.4")["site_access"] == "RATE_LIMITED"
    page = "<table><tr><td>IP 地址</td><td>1.2.3.4</td></tr>"
    for k, v in [
        ("IP 位置", "美国"),
        ("ASN", "AS123"),
        ("ASN 所有者", "Sample ISP"),
        ("企业", "Sample"),
        ("IP 类型", "家庭宽带"),
        ("风控值", "10% 低风险"),
        ("原生 IP", "原生"),
        ("共享人数", "1–10"),
    ]:
        page += f"<tr><td>{k}</td><td>{v}</td></tr>"
    page += "</table>"
    result = parse_page(page.encode(), 200, "1.2.3.4")
    assert result["state"] == "READY" and result["risk_percent"] == 10
    assert parse_page(page.encode(), 200, "1.2.3.5")["site_access"] == "UNVERIFIED"


async def test_ping0_diagnostic_preserves_active_binding_and_caches_challenge(gateway, monkeypatch):
    binding, node, _ = await gateway.acquire(route())
    gateway.report_methods["ping0"] = True
    before = node.state, node.used, node.binding_id, binding.session
    calls = 0

    async def execute(node):
        nonlocal calls
        calls += 1
        return {
            "ip": node.ip,
            "state": "UNAVAILABLE",
            "site_access": "CHALLENGE",
            "fields": {},
            "note": "验证页",
        }

    monkeypatch.setattr(gateway.ping0, "execute", execute)
    result = await gateway.ping0.run(node.id)
    assert result["site_access"] == "CHALLENGE"
    assert before == (node.state, node.used, node.binding_id, binding.session)
    assert not gateway.replacements
    gateway.ping0.next_at = 0
    assert (await gateway.ping0.run(node.id))["cached"] and calls == 1


async def test_event_timeline_preserves_order_and_session_request(client, gateway):
    b, old, _ = await gateway.acquire(route())
    _, fresh, _ = await gateway.acquire(route("two"))
    response = await client.get(
        "/api/v1/lifecycle/chains",
        params={"event_view": True, "view": "ips", "q": old.ip, "order": "asc", "page_size": 50},
        headers={"Authorization": "Bearer test-api-token"},
    )
    assert response.status_code == 200
    rows = response.json()["items"]
    assert [e["at"] for e in rows] == sorted(e["at"] for e in rows)
    request = next(e for e in rows if e["kind"] == "session_requested")
    switch = next(e for e in rows if e["kind"] == "switched")
    assert request["session"] == "two" and request["old_session"] == "one"
    assert switch["ip"] == fresh.ip and switch["old_ip"] == old.ip
    assert all(e.get("country") == "US" for e in rows if e.get("ip"))
    assert rows.index(request) < rows.index(switch)
