import asyncio
import json
from dataclasses import replace

import pytest

from rapip.config import private_json
from rapip.gateway import Gateway
from rapip.models import GatewayError, IPState, Replacement, Route
from rapip.provider import ProviderError
from rapip.storage import Storage

HEADERS = {"Authorization": "Bearer test-api-token"}


async def pause(client):
    response = await client.patch("/api/v1/accounts/test", headers=HEADERS, json={"enabled": False})
    assert response.status_code == 200


async def test_edit_account_metadata_does_not_reset_usage_or_credentials(client, gateway):
    gateway.counters["test"]["submitted"] = 5
    private_json(gateway.config.config_dir / "secrets.json", {"test": "original-private-api-key"})
    response = await client.patch(
        "/api/v1/accounts/test",
        headers=HEADERS,
        json={
            "name": "新名称",
            "traffic_limit_gb": 250,
            "replacement_budget": None,
            "replacement_country": "CA",
        },
    )
    assert response.status_code == 200
    account = gateway.accounts["test"]
    assert account.name == "新名称" and account.traffic_limit_gb == 250 and account.replacement_budget is None
    assert gateway.counters["test"]["submitted"] == 5
    assert gateway.config.key(account) == "original-private-api-key"
    assert "original-private-api-key" not in (await client.get("/api/v1/accounts", headers=HEADERS)).text
    assert gateway.test_provider.calls == 0


async def test_key_update_requires_paused_idle_account_and_matching_resources(client, gateway):
    key = "rotated-private-api-key"
    assert (
        await client.patch("/api/v1/accounts/test", headers=HEADERS, json={"api_key": key})
    ).status_code == 409
    await pause(client)
    response = await client.patch("/api/v1/accounts/test", headers=HEADERS, json={"api_key": key})
    assert response.status_code == 200 and gateway.config.key(gateway.accounts["test"]) == key
    gateway.test_provider.rows = []
    mismatch = await client.patch(
        "/api/v1/accounts/test", headers=HEADERS, json={"api_key": "different-resources-key"}
    )
    assert mismatch.status_code == 409
    assert gateway.config.key(gateway.accounts["test"]) == key
    assert gateway.test_provider.calls == 0


async def test_invalid_key_preserves_configuration_and_is_not_echoed(client, gateway, monkeypatch):
    await pause(client)

    async def fail():
        raise ProviderError(401)

    monkeypatch.setattr(gateway.test_provider, "list_nodes", fail)
    response = await client.patch(
        "/api/v1/accounts/test", headers=HEADERS, json={"api_key": "invalid-private-api-key"}
    )
    assert response.status_code == 400 and "invalid-private-api-key" not in response.text
    assert gateway.config.key(gateway.accounts["test"]) == ""


async def test_delete_requires_pause_and_rejects_live_binding(client, gateway):
    assert not (await client.get("/api/v1/accounts/test/deletion-check", headers=HEADERS)).json()["allowed"]
    with pytest.raises(GatewayError, match="先暂停"):
        await gateway.remove_account("test")
    await gateway.acquire(Route.parse("admin-worker-01-session-active"))
    await pause(client)
    with pytest.raises(GatewayError, match="活跃"):
        await gateway.remove_account("test")
    assert "test" in gateway.accounts and not gateway.removed_accounts


async def test_delete_rejects_replacement_or_diagnostic_in_progress(client, gateway):
    await pause(client)
    node = next(iter(gateway.nodes.values()))
    gateway.replacements["pending"] = Replacement("pending", node.id, "test", state="UNCERTAIN")
    with pytest.raises(GatewayError, match="替换"):
        await gateway.remove_account("test")
    gateway.replacements.clear()
    gateway.ipquality.running = node.id
    with pytest.raises(GatewayError, match="检测"):
        await gateway.remove_account("test")
    gateway.ipquality.running = None


async def test_delete_archives_nodes_scrubs_local_secrets_and_preserves_budget(client, gateway):
    await pause(client)
    gateway.counters["test"]["submitted"] = 7
    private_json(
        gateway.config.config_dir / "secrets.json", {"test": "delete-local-key", "other": "keep-other-key"}
    )
    result = await gateway.remove_account("test")
    assert result["archived_nodes"] == 6 and result["replace_calls"] == 0
    assert not gateway.accounts and gateway.counters["test"]["submitted"] == 7
    assert all(
        n.state == IPState.ARCHIVED and not n.username and not n.password for n in gateway.nodes.values()
    )
    assert json.loads((gateway.config.config_dir / "secrets.json").read_text()) == {"other": "keep-other-key"}
    assert gateway.config.accounts() == [] and gateway.test_provider.calls == 0
    assert "test" in (await gateway.storage.load())["removed_accounts"]
    await gateway.sync_account("test")  # A sync queued before deletion must safely become a no-op.
    readd = await client.post(
        "/api/v1/accounts",
        headers=HEADERS,
        json={"account_id": "test", "name": "repeat", "api_key": "cannot-reset-budget-key"},
    )
    assert readd.status_code == 409


async def test_delete_marker_survives_cleanup_failure_and_startup_retries(
    client, gateway, monkeypatch, tmp_path
):
    await pause(client)
    private_json(gateway.config.config_dir / "secrets.json", {"test": "pending-cleanup-key"})
    original = gateway._cleanup_removed_account_files

    def fail():
        raise OSError("disk temporarily unavailable")

    monkeypatch.setattr(gateway, "_cleanup_removed_account_files", fail)
    result = await gateway.remove_account("test")
    assert result["warning"] and gateway.config.accounts()
    gateway.reload_accounts()
    assert not gateway.accounts
    snapshot = await gateway.storage.load()
    config = replace(gateway.config, data_dir=tmp_path / "restored-data")
    storage = Storage(config.data_dir)
    await storage.open()
    await storage.save(snapshot)
    await storage.close()
    restored = Gateway(config, provider_factory=gateway.provider_factory, probe=gateway.probe)
    try:
        await restored.start(background=False, sync=False)
        assert not restored.accounts and config.accounts() == []
        assert json.loads((config.config_dir / "secrets.json").read_text()) == {}
    finally:
        await restored.stop()
    monkeypatch.setattr(gateway, "_cleanup_removed_account_files", original)


async def test_delete_api_auth_csrf_idempotency_and_account_scope(client, gateway):
    path = "/api/v1/accounts/test"
    assert (await client.delete(path)).status_code == 401
    assert (await client.get(path + "/deletion-check")).status_code == 401
    await pause(client)
    headers = {**HEADERS, "Idempotency-Key": "account-local-delete-1"}
    first = await client.delete(path, headers=headers)
    assert first.status_code == 202
    for _ in range(100):
        op = gateway.operations[first.json()["id"]]
        if op.state in {"COMPLETED", "FAILED"}:
            break
        await asyncio.sleep(0.01)
    assert op.state == "COMPLETED" and op.account_id == "test"
    second = await client.delete(path, headers=headers)
    assert second.json()["id"] == first.json()["id"]
    await client.post("/api/v1/auth/login", json={"username": "admin", "password": "test-admin-password"})
    assert (await client.delete(path)).status_code == 403
    assert gateway.test_provider.calls == 0
