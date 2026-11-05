import asyncio
import copy
import time

import pytest

from rapip.ipquality import normalize_report
from rapip.models import GatewayError

HEADERS = {"Authorization": "Bearer test-api-token"}


def raw(ip):
    return {
        "Head": {"IP": ip},
        "Info": {"ASN": "123", "Organization": "Test ISP"},
        "Type": {"Usage": {"IPinfo": "ISP"}},
        "Score": {"SCAMALYTICS": "0", "IPQS": "null"},
        "Factor": {"VPN": {"IPinfo": False, "IPQS": None}},
    }


def test_report_preserves_zero_false_and_unknown_without_admitting():
    report = normalize_report(raw("198.51.100.10"), "198.51.100.10")
    assert report["Score"]["SCAMALYTICS"] == "0"
    assert report["Score"]["IPQS"] is None
    assert report["Factor"]["VPN"]["IPinfo"] is False
    assert "IPQS" in report["missing_scores"] and report["state"] == "PARTIAL"
    assert normalize_report({"Head": {"IP": "198.51.100.10"}}, "198.51.100.10")["state"] == "UNKNOWN"
    with pytest.raises(ValueError):
        normalize_report(raw("198.51.100.11"), "198.51.100.10")


async def test_ipquality_is_readonly_and_cached_without_business_binding(gateway, monkeypatch):
    node = next(iter(gateway.nodes.values()))
    before = copy.deepcopy(node.public())

    async def execute(snapshot):
        assert snapshot.id == node.id and snapshot is not node
        return raw(snapshot.ip)

    monkeypatch.setattr(gateway.ipquality, "execute", execute)
    result = await gateway.ipquality.run(node.id)
    assert result["replace_calls"] == 0 and result["node_id"] == node.id
    assert node.public() == before and not gateway.bindings and gateway.test_provider.calls == 0
    gateway.ipquality.cache.clear()
    assert (await gateway.ipquality.latest(node))["Score"]["SCAMALYTICS"] == "0"
    gateway.observer.retention.probe_log_days = 1
    async with gateway.observer.lock:
        await gateway.observer.db.execute(
            "UPDATE records SET at=? WHERE kind='ipquality'", (time.time() - 2 * 86400,)
        )
        await gateway.observer.db.commit()
    assert await gateway.ipquality.latest(node) is None


async def test_ipquality_concurrency_and_stale_report_are_guarded(gateway, monkeypatch):
    node = next(iter(gateway.nodes.values()))
    began, finish = asyncio.Event(), asyncio.Event()

    async def execute(snapshot):
        began.set()
        await finish.wait()
        return raw(snapshot.ip)

    monkeypatch.setattr(gateway.ipquality, "execute", execute)
    task = asyncio.create_task(gateway.ipquality.run(node.id))
    await began.wait()
    with pytest.raises(GatewayError, match="已有"):
        await gateway.ipquality.run(node.id)
    node.membership_version += 1
    finish.set()
    with pytest.raises(GatewayError, match="资源发生变化"):
        await task
    assert gateway.ipquality.running is None and not gateway.ipquality.cache
    assert gateway.test_provider.calls == 0


async def test_ipquality_api_auth_csrf_idempotency_and_key_redaction(client, gateway, monkeypatch):
    node = next(iter(gateway.nodes.values()))
    calls = []

    async def execute(snapshot):
        calls.append(snapshot.id)
        return raw(snapshot.ip)

    monkeypatch.setattr(gateway.ipquality, "execute", execute)
    path = f"/api/v1/nodes/{node.id}/ip-quality"
    assert (await client.get(path)).status_code == 401
    assert (await client.post(path)).status_code == 401
    headers = {**HEADERS, "Idempotency-Key": "ipquality-same-operation"}
    response = await client.post(path, headers=headers, json={})
    assert response.status_code == 202
    for _ in range(50):
        if gateway.operations[response.json()["id"]].state in {"COMPLETED", "FAILED"}:
            break
        await asyncio.sleep(0.01)
    again = await client.post(path, headers=headers, json={})
    assert again.json()["id"] == response.json()["id"] and calls == [node.id]
    data = (await client.get(path, headers=HEADERS)).json()
    assert data["report"]["ip"] == node.ip and not data["running"]
    assert node.password not in str(data) and gateway.config.admin_token not in str(data)
    login = await client.post(
        "/api/v1/auth/login", json={"username": "admin", "password": gateway.config.admin_password}
    )
    assert login.status_code == 200
    assert (await client.post(path, json={})).status_code == 403


async def test_connection_limits_are_editable_with_validation(client, gateway):
    invalid = await client.patch("/api/v1/settings", headers=HEADERS, json={"max_connections_per_binding": 0})
    assert invalid.status_code == 422
    response = await client.patch(
        "/api/v1/settings",
        headers=HEADERS,
        json={"max_connections_per_binding": 256, "max_connections": 1024},
    )
    assert response.status_code == 200
    assert gateway.settings.max_connections_per_binding == 256
    assert (await gateway.storage.load())["settings"]["max_connections_per_binding"] == 256
