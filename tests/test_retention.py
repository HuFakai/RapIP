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


async def test_trace_retention_is_independent_and_capacity_report_is_complete(client, gateway):
    now = time.time()
    gateway.observer.retention = RetentionPolicy(event_days=1, trace_days=7)
    gateway.lifecycle.add("assigned", ip="192.0.2.10", group_id="default", at=now - 3 * 86400)
    gateway.events.append({"at": now - 3 * 86400, "kind": "old-runtime"})
    await gateway.cleanup_history()
    assert not any(e.get("kind") == "old-runtime" for e in gateway.events)
    rows = await gateway.storage.history("ips", {}, 0, 100, now - 7 * 86400)
    assert any(e.get("ip") == "192.0.2.10" for e in rows[0])
    status = (await client.get("/api/v1/settings/storage", headers=HEADERS)).json()
    assert status["trace_records"] > 0
    assert status["database_bytes"] == status["observation_database_bytes"] + status["state_database_bytes"]
    assert status["policy"]["metrics_interval_seconds"] == 60


async def test_minute_noise_aggregation_keeps_true_attempt_counts(gateway):
    obs = gateway.observer
    obs.mode = "all"
    for _ in range(5):
        obs.finish(obs.begin("CONNECT"), "authentication_rejected")
    await obs.flush(gateway)
    rows = await obs.records("connection", {})
    assert rows["total"] == 1 and rows["items"][0]["repeat_count"] == 5
    obs.finish(obs.begin("CONNECT"), "authentication_rejected")
    await obs.flush(gateway)
    rows = await obs.records("connection", {})
    assert rows["total"] == 1 and rows["items"][0]["repeat_count"] == 6
    metrics = await obs.metrics()
    assert metrics["summary"]["attempts"] == 6 and metrics["summary"]["failures"] == 6
    # Valid routes still have individual records, even if they fail before dialing.
    for i in range(2):
        item = obs.begin("CONNECT")
        item.update(group_id="default", worker="01", session=f"bad{i}")
        obs.finish(item, "SESSION_RETIRED")
    await obs.flush(gateway)
    assert (await obs.records("connection", {}))["total"] == 3


async def test_compaction_merges_overlapping_precision_without_losing_counters(gateway):
    obs = gateway.observer
    bucket = int((time.time() - 90000) // 60) * 60
    for resolution, up in ((10, 100), (30, 200), (60, 300)):
        await obs.db.execute(
            "INSERT INTO samples VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (bucket, resolution, "global", "", up, 0, 1, 1, 0, 0, 0, 5, 1, "{}", "test"),
        )
    await obs.db.commit()
    await obs.compact(time.time())
    async with obs.db.execute("SELECT sum(up),sum(attempts),count(*) FROM samples WHERE scope='global'") as c:
        assert await c.fetchone() == (600, 3, 1)
    await obs.compact(time.time())
    async with obs.db.execute("SELECT sum(up),sum(attempts) FROM samples WHERE scope='global'") as c:
        assert await c.fetchone() == (600, 3)


async def test_deleted_group_names_are_archived_and_recovered(gateway):
    from rapip.quality import QualityManager

    await gateway.quality.save_group("oldgroup", {"name": "数据项目"}, [], create=True)
    await gateway.quality.delete("oldgroup")
    assert gateway.quality.archived_groups["oldgroup"] == "数据项目"
    restored = QualityManager(gateway)
    restored.load(gateway.quality.snapshot())
    assert restored.archived_groups["oldgroup"] == "数据项目"
    gateway.operations["historic"] = Operation(
        "historic", "group_create", state="COMPLETED", result={"id": "legacy", "name": "原分组名称"}
    )
    restored.load({"groups": {}})
    assert restored.archived_groups["legacy"] == "原分组名称"


async def test_noise_arriving_during_flush_is_not_discarded(gateway, monkeypatch):
    obs = gateway.observer
    obs.finish(obs.begin("CONNECT"), "authentication_rejected")
    execute = obs.db.execute
    injected = False

    async def interleave(sql, *args, **kwargs):
        nonlocal injected
        if "INSERT INTO records" in sql and not injected:
            injected = True
            obs.finish(obs.begin("CONNECT"), "authentication_rejected")
        return await execute(sql, *args, **kwargs)

    # Only replace during flush, where execute is awaited rather than a context manager.
    monkeypatch.setattr(obs.db, "execute", interleave)
    obs.last_compact = time.time()
    await obs.flush(gateway)
    monkeypatch.setattr(obs.db, "execute", execute)
    assert len([r for kind, r in obs.queue if kind == "connection"]) == 1
    await obs.flush(gateway)
    rows = await obs.records("connection", {})
    assert rows["items"][0]["repeat_count"] == 2
    assert (await obs.metrics())["summary"]["attempts"] == 2


async def test_trace_cap_applies_when_policy_changes_without_new_events(gateway):
    records = [{"id": str(i), "kind": "test", "at": float(i)} for i in range(1005)]
    await gateway.storage.save(gateway._snapshot(), records, trace_limit=20000)
    await gateway.storage.save(gateway._snapshot(), trace_limit=1000)
    async with gateway.storage.db.execute("SELECT count(*),min(at) FROM lifecycle") as c:
        count, oldest = await c.fetchone()
    assert count == 1000 and oldest >= 5
