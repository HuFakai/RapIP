import asyncio
from dataclasses import replace

import httpx

from rapip.api import create_app
from rapip.models import Route


async def test_unauthenticated_cannot_see_private_resources(client):
    assert (await client.get("/api/v1/stats")).status_code == 401
    assert (await client.get("/api/v1/nodes")).status_code == 401
    assert (await client.get("/api/v1/health/live")).status_code == 200


async def test_cookie_login_csrf_and_logout(client):
    result = await client.post(
        "/api/v1/auth/login", json={"username": "admin", "password": "test-admin-password"}
    )
    assert result.status_code == 200 and "HttpOnly" in result.headers["set-cookie"]
    assert (await client.get("/api/v1/stats")).json()["total_ips"] == 6
    assert (await client.patch("/api/v1/settings", json={"max_active_bindings": 2})).status_code == 403
    headers = {"x-csrf-token": result.json()["csrf"]}
    assert (
        await client.patch("/api/v1/settings", headers=headers, json={"max_active_bindings": 2})
    ).status_code == 200
    assert (await client.post("/api/v1/auth/logout", headers=headers)).status_code == 200
    assert (await client.get("/api/v1/stats")).status_code == 401


async def test_cross_origin_login_rejected(client):
    response = await client.post(
        "/api/v1/auth/login",
        headers={"origin": "https://untrusted.example"},
        json={"username": "admin", "password": "test-admin-password"},
    )
    assert response.status_code == 403


async def test_bearer_api_and_no_secrets(client):
    headers = {"authorization": "Bearer test-api-token"}
    response = await client.get("/api/v1/nodes", headers=headers)
    assert response.status_code == 200
    assert "upstream-secret" not in response.text and '"password"' not in response.text
    assert (await client.get("/api/v1/nodes?page=0", headers=headers)).status_code == 400


async def test_idempotent_rotation_is_only_applied_once(client, gateway):
    binding, old, _ = await gateway.acquire(Route("admin", "aaa", "01"))
    headers = {"authorization": "Bearer test-api-token", "Idempotency-Key": "same-operation-key"}
    body = {"mode": "hard", "expected_generation": 1}
    path = f"/api/v1/bindings/{binding.id}/rotate"
    first = await client.post(path, json=body, headers=headers)
    second = await client.post(path, json=body, headers=headers)
    assert first.status_code == second.status_code == 202
    assert first.json()["id"] == second.json()["id"]
    for _ in range(20):
        await asyncio.sleep(0.01)
        result = await client.get(f"/api/v1/operations/{first.json()['id']}", headers=headers)
        if result.json()["state"] == "COMPLETED":
            break
    assert binding.generation == 2 and binding.node_id != old.id
    assert len(gateway.replacements) == 1
    conflict = await client.post(path, json={"mode": "graceful"}, headers=headers)
    assert conflict.status_code == 409


async def test_validation_does_not_echo_api_key(client):
    response = await client.post(
        "/api/v1/accounts",
        headers={"authorization": "Bearer test-api-token"},
        json={"account_id": "bad!", "name": "test", "api_key": "SECRET_SHOULD_NOT_BE_ECHOED"},
    )
    assert response.status_code == 422
    assert "SECRET_SHOULD_NOT_BE_ECHOED" not in response.text


async def test_unknown_api_not_replaced_by_frontend(client):
    assert (await client.get("/api/v1/not-a-route")).status_code == 404


async def test_same_host_different_ports_keep_independent_admin_sessions(gateway):
    first = create_app(replace(gateway.config, http_port=19080), gateway, lifecycle=False)
    second = create_app(replace(gateway.config, http_port=19081), gateway, lifecycle=False)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=first), base_url="http://samehost:19080"
    ) as a:
        login_a = await a.post(
            "/api/v1/auth/login", json={"username": "admin", "password": "test-admin-password"}
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=second), base_url="http://samehost:19081", cookies=a.cookies
        ) as b:
            login_b = await b.post(
                "/api/v1/auth/login", json={"username": "admin", "password": "test-admin-password"}
            )
            assert login_a.headers["set-cookie"].split("=")[0] != login_b.headers["set-cookie"].split("=")[0]
            a.cookies.update(b.cookies)
            assert (await a.get("/api/v1/stats")).status_code == 200
            assert (await b.get("/api/v1/stats")).status_code == 200
