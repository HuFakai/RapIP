import time

from rapip.models import Operation
from rapip.retention import RetentionPolicy

HEADERS = {"Authorization": "Bearer test-api-token"}


async def test_configurable_retention_separates_kinds_and_keeps_fresh_data(gateway):
    obs = gateway.observer
    now = time.time()
    obs.retention = RetentionPolicy(success_log_hours=1, error_log_days=2, probe_log_days=3, metrics_days=7)
    entries = [
        ("old-success", "connection", "success", 7200),
        ("recent-error", "connection", "failed", 86400),
        ("old-error", "connection", "failed", 3 * 86400),
        ("recent-probe", "probe", "success", 2 * 86400),
        ("old-probe", "probe", "success", 4 * 86400),
    ]
    for id, kind, result, age in entries:
        obs.enqueue(kind, {"id": id, "at": now - age, "result": result})
    await obs.flush(gateway)
    async with obs.db.execute(
        "SELECT id FROM records WHERE id LIKE '%-success' OR id LIKE '%-error' OR id LIKE '%-probe'"
    ) as cursor:
        assert {row[0] for row in await cursor.fetchall()} == {"recent-error", "recent-probe"}
    assert gateway.test_provider.calls == 0


async def test_completed_history_keeps_inflight_and_idempotent_results(gateway):
    now = time.time()
    gateway.observer.retention = RetentionPolicy(operation_days=1, event_days=1)
    gateway.operations = {
        "old": Operation("old", "test", state="COMPLETED", completed_at=now - 3 * 86400),
        "failed": Operation("failed", "test", state="FAILED", completed_at=now - 3 * 86400),
        "running": Operation("running", "test", state="RUNNING", created_at=now - 10 * 86400),
        "protected": Operation("protected", "test", state="COMPLETED", completed_at=now - 3 * 86400),
    }
    gateway.idempotency["keep"] = {"id": "protected", "expires": now + 3600, "fingerprint": "same"}
    gateway.events = [{"at": now - 3 * 86400}, {"at": now}]
    before = gateway.quality.snapshot()
    result = await gateway.cleanup_history()
    assert result["removed_operations"] == 2
    assert set(gateway.operations) == {"running", "protected"}
    assert len(gateway.events) == 1
    assert gateway.quality.snapshot() == before
    assert gateway.test_provider.calls == 0


async def test_policy_validation_persistence_and_cleanup_auth(client, gateway):
    assert (await client.get("/api/v1/settings/storage")).status_code == 401
    assert (await client.post("/api/v1/settings/storage/cleanup")).status_code == 401
    invalid = await client.patch("/api/v1/settings", headers=HEADERS, json={"retention": {"metrics_days": 0}})
    assert invalid.status_code == 422
    response = await client.patch(
        "/api/v1/settings",
        headers=HEADERS,
        json={"retention": {"success_log_hours": 12, "operation_days": 14}},
    )
    assert response.status_code == 200
    assert gateway._snapshot()["retention"]["success_log_hours"] == 12
    saved = await gateway.storage.load()
    assert saved["retention"]["operation_days"] == 14
    status = (await client.get("/api/v1/settings/storage", headers=HEADERS)).json()
    assert status["policy"]["success_log_hours"] == 12 and status["database_bytes"] >= 0
    assert (await client.get("/api/v1/settings", headers=HEADERS)).json()["values"]["retention"] == status[
        "policy"
    ]


async def test_auto_generated_group_id_is_stable_on_idempotent_retry(client, gateway):
    import asyncio

    headers = {**HEADERS, "Idempotency-Key": "auto-group-test-1"}
    first = await client.post("/api/v1/groups", headers=headers, json={"name": "自动 ID"})
    assert first.status_code == 202, first.text
    await asyncio.sleep(0.05)
    again = await client.post("/api/v1/groups", headers=headers, json={"name": "自动 ID"})
    assert first.json()["id"] == again.json()["id"]
    groups = [group for group in gateway.quality.groups.values() if group["name"] == "自动 ID"]
    assert len(groups) == 1 and groups[0]["id"].startswith("g")
    assert gateway.test_provider.calls == 0


def test_paused_details_are_bounded_without_stopping_counters(gateway):
    obs = gateway.observer
    obs.detail_paused = True
    before = len(obs.queue)
    count_before = obs.counts[("global", "")]["probes"]
    obs.enqueue("probe", {"id": "paused", "at": time.time()})
    obs.add("probes")
    assert len(obs.queue) == before and obs.dropped >= 1
    assert obs.counts[("global", "")]["probes"] == count_before + 1


async def test_expired_idempotency_never_references_pruned_operation(gateway):
    now = time.time()
    gateway.operations["expired"] = Operation(
        "expired", "test", state="COMPLETED", completed_at=now - 40 * 86400
    )
    gateway.idempotency["expired-key"] = {"id": "expired", "expires": now - 1, "fingerprint": "old"}
    gateway._prune_history(now)
    assert "expired-key" not in gateway.idempotency
    assert "expired" not in gateway.operations


async def test_ai_prompt_matches_group_and_omits_credentials(client, gateway):
    await gateway.quality.save_group("collect", {"name": "collect"}, [], create=True)
    response = await client.post(
        "/api/v1/connection-config",
        headers=HEADERS,
        json={"group_id": "collect", "session_id": "batch01", "host": "140.245.36.206"},
    )
    assert response.status_code == 200
    guide = response.json()["ai_guide"]
    assert "admin-group-collect-worker-01-session-batch01" in guide
    assert "GET /api/v1/bindings?group_id=collect" in guide
    assert "UNKNOWN" in guide and "幂等" in guide and "保留期限" in guide
    assert gateway.config.socks_password not in guide and gateway.config.admin_token not in guide
