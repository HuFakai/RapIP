import asyncio
import copy
import time
from datetime import datetime, timezone

import pytest

from rapip.provider import ProviderError, Webshare
from rapip.quotas import aggregate_usage, normalize_usage, public_usage

HEADERS = {"Authorization": "Bearer test-api-token"}


def data(plan_id=1, subscription_id=1):
    now = time.time()
    return (
        {
            "id": subscription_id,
            "plan": plan_id,
            "start_date": datetime.fromtimestamp(now - 86400, timezone.utc).isoformat(),
            "end_date": datetime.fromtimestamp(now + 29 * 86400, timezone.utc).isoformat(),
        },
        {
            "id": plan_id,
            "bandwidth_limit": 250.0,
            "proxy_replacements_total": 1000,
            "proxy_replacements_used": 1,
            "proxy_replacements_available": 999,
        },
        {"bandwidth_total": 5_000_000, "bandwidth_projected": 200_000_000_000},
    )


def snapshot(plan_id=1, subscription_id=1):
    return normalize_usage(*data(plan_id, subscription_id))


def test_normalize_uses_actual_period_usage_not_projected_or_local_count():
    s = snapshot()
    assert s["replacement"] == {"total": 1000, "used": 1, "remaining": 999}
    assert s["traffic"]["total_bytes"] == 250_000_000_000
    assert s["traffic"]["remaining_bytes"] == 249_995_000_000
    assert not public_usage(s)["stale"]


def test_unknown_and_unlimited_and_overage_are_distinct():
    sub, plan, stats = data()
    plan["bandwidth_limit"] = 0
    unlimited = normalize_usage(sub, plan, stats)
    assert unlimited["traffic"]["unlimited"] and unlimited["traffic"]["remaining_bytes"] is None
    plan.pop("bandwidth_limit")
    plan.pop("proxy_replacements_available")
    unknown = normalize_usage(sub, plan, stats)
    assert unknown["traffic"]["total_bytes"] is None and not unknown["traffic"]["unlimited"]
    assert unknown["replacement"]["remaining"] is None
    plan["bandwidth_limit"] = 1
    stats["bandwidth_total"] = 2_000_000_000
    assert normalize_usage(sub, plan, stats)["traffic"]["remaining_bytes"] == 0


def test_invalid_supplier_period_and_values_fail_closed():
    sub, plan, stats = data()
    plan["proxy_replacements_available"] = 1001
    assert normalize_usage(sub, plan, stats)["replacement"]["remaining"] is None
    plan["bandwidth_limit"] = True
    assert normalize_usage(sub, plan, stats)["traffic"]["total_bytes"] is None
    sub["end_date"] = sub["start_date"]
    with pytest.raises(ValueError):
        normalize_usage(sub, plan, stats)


def test_aggregate_sums_every_account_not_just_table_first_page_and_deduplicates():
    accounts = [{"supplier_usage": snapshot(i, i)} for i in range(1, 13)]
    accounts.append(copy.deepcopy(accounts[0]))
    result = aggregate_usage(accounts)
    assert result["account_count"] == 13 and result["unique_plans"] == 12
    assert result["duplicate_accounts"] == 1
    assert result["replacement"]["remaining"] == 12 * 999
    assert result["traffic"]["total_bytes"] == 12 * 250_000_000_000
    assert result["replacement"]["complete"]


def test_aggregate_excludes_stale_expired_and_unknown_instead_of_zero():
    fresh, stale = snapshot(), snapshot(2, 2)
    stale["checked_at"] = time.time() - 901
    result = aggregate_usage([{"supplier_usage": fresh}, {"supplier_usage": stale}, {}])
    assert result["replacement"]["remaining"] == 999 and not result["replacement"]["complete"]
    assert result["missing_accounts"] == 2
    empty = aggregate_usage([{}])
    assert empty["traffic"]["remaining_bytes"] is None and empty["replacement"]["total"] is None
    assert public_usage(fresh, now=time.time() + 32 * 86400)["period_expired"]


def test_unlimited_totals_preserve_finite_component_and_coverage():
    sub, plan, stats = data(2, 2)
    plan["bandwidth_limit"] = 0
    result = aggregate_usage(
        [{"supplier_usage": snapshot()}, {"supplier_usage": normalize_usage(sub, plan, stats)}]
    )
    assert result["traffic"]["unlimited_plans"] == 1 and result["traffic"]["complete"]
    assert result["traffic"]["total_bytes"] == 250_000_000_000


async def test_provider_usage_queries_get_only_correct_plan_period_and_no_secrets(gateway, monkeypatch):
    provider = Webshare(gateway.accounts["test"], "never-expose-this-token", gateway.client)
    sub, plan, stats = data(123)
    calls = []

    async def request(method, path, **kwargs):
        calls.append((method, path, kwargs))
        return sub if path == "/api/v2/subscription/" else plan if "/plan/" in path else stats

    monkeypatch.setattr(provider, "request", request)
    result = await provider.usage()
    assert len(calls) == 3 and all(c[0] == "GET" for c in calls)
    assert calls[1][1] == "/api/v2/subscription/plan/123/"
    assert calls[2][2]["params"]["timestamp__gte"] == sub["start_date"]
    assert "never-expose" not in str(result)
    assert result["traffic"]["used_bytes"] == stats["bandwidth_total"]


async def test_provider_rejects_configured_inactive_plan(gateway, monkeypatch):
    from dataclasses import replace

    provider = Webshare(replace(gateway.accounts["test"], plan_id="999"), "token", gateway.client)
    sub, plan, _ = data(123)

    async def request(method, path, **kwargs):
        return sub if "/plan/" not in path else {**plan, "id": 999}

    monkeypatch.setattr(provider, "request", request)
    with pytest.raises(ValueError):
        await provider.usage()


async def test_usage_sync_is_cached_persisted_and_failure_does_not_block_nodes(gateway, monkeypatch):
    count = 0

    async def usage():
        nonlocal count
        count += 1
        return snapshot()

    monkeypatch.setattr(gateway.test_provider, "usage", usage, raising=False)
    await gateway.sync_usage("test")
    await gateway.sync_usage("test")
    assert count == 1 and (await gateway.storage.load())["supplier_usage"]["test"]["plan_id"] == "1"
    assert gateway.counters["test"]["submitted"] == 0
    old = gateway.supplier_usage["test"]
    old["last_attempt"] -= 301

    async def fail():
        raise ProviderError(403)

    monkeypatch.setattr(gateway.test_provider, "usage", fail)
    result = await gateway.sync_usage("test")
    assert result["stale"] and result["replacement"]["remaining"] == 999
    assert gateway.account_health["test"]["state"] == "healthy" and gateway.stats()["fresh_count"] == 6
    assert gateway.test_provider.calls == 0


async def test_usage_result_rejected_if_account_changes_while_loading(gateway, monkeypatch):
    from dataclasses import replace

    gateway.supplier_usage.clear()
    began, finish = asyncio.Event(), asyncio.Event()

    async def usage():
        began.set()
        await finish.wait()
        return snapshot()

    monkeypatch.setattr(gateway.test_provider, "usage", usage, raising=False)
    task = asyncio.create_task(gateway.sync_usage("test"))
    await began.wait()
    gateway.accounts["test"] = replace(gateway.accounts["test"], name="renamed")
    finish.set()
    assert (await task)["available"] is False
    assert "test" not in gateway.supplier_usage


async def test_account_auto_id_optional_notes_and_default_compatibility(client, gateway):
    body = {"name": "新增账号", "api_key": "a-private-test-account-key", "notes": "项目 A\n备用渠道"}
    headers = {**HEADERS, "Idempotency-Key": "create-auto-account-01"}
    response = await client.post("/api/v1/accounts", headers=headers, json=body)
    assert response.status_code == 201
    id = response.json()["account_id"]
    assert id.startswith("a") and id.isalnum()
    assert gateway.accounts[id].notes == body["notes"]
    assert (await client.post("/api/v1/accounts", headers=headers, json=body)).status_code == 409
    assert len(gateway.accounts) == 2
    patch = await client.patch(f"/api/v1/accounts/{id}", headers=HEADERS, json={"notes": "备注更新"})
    assert patch.status_code == 200 and gateway.accounts[id].notes == "备注更新"
    assert gateway.accounts["test"].notes == ""
    long_note = await client.patch(f"/api/v1/accounts/{id}", headers=HEADERS, json={"notes": "x" * 501})
    assert long_note.status_code == 422 and gateway.test_provider.calls == 0


async def test_overview_quota_scopes_and_refresh_auth(client, gateway, monkeypatch):
    gateway.supplier_usage["test"] = snapshot()
    response = await client.get("/api/v1/overview", headers=HEADERS)
    assert response.status_code == 200 and response.json()["supplier_totals"]["replacement"]["total"] == 1000
    scoped = await client.get("/api/v1/overview?account_id=absent", headers=HEADERS)
    assert scoped.json()["supplier_totals"]["account_count"] == 0
    assert (await client.post("/api/v1/accounts/test/refresh-usage")).status_code == 401
    await client.post("/api/v1/auth/login", json={"username": "admin", "password": "test-admin-password"})
    assert (await client.post("/api/v1/accounts/test/refresh-usage")).status_code == 403


async def test_refresh_usage_idempotency_and_gets_never_call_supplier(client, gateway, monkeypatch):
    calls = []

    async def usage():
        calls.append(1)
        return snapshot()

    monkeypatch.setattr(gateway.test_provider, "usage", usage, raising=False)
    await client.get("/api/v1/accounts", headers=HEADERS)
    await client.get("/api/v1/overview", headers=HEADERS)
    assert not calls
    headers = {**HEADERS, "Idempotency-Key": "usage-refresh-same-operation"}
    path = "/api/v1/accounts/test/refresh-usage"
    response = await client.post(path, headers=headers)
    for _ in range(100):
        op = gateway.operations[response.json()["id"]]
        if op.state in {"COMPLETED", "FAILED"}:
            break
        await asyncio.sleep(0.01)
    assert op.state == "COMPLETED" and len(calls) == 1
    second = await client.post(path, headers=headers)
    assert second.json()["id"] == response.json()["id"]
    assert len(calls) == 1 and gateway.test_provider.calls == 0


def test_expired_period_balance_is_hidden_without_mutating_stored_evidence():
    s = snapshot()
    before = copy.deepcopy(s)
    public = public_usage(s, time.time() + 32 * 86400)
    assert public["replacement"]["remaining"] is None and public["traffic"]["remaining_bytes"] is None
    assert public["period_expired"] and s == before
