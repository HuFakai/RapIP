import json
import stat

import httpx

from rapip.config import Config
from rapip.credentials import Credentials
from rapip.models import Route

HEADERS = {"Authorization": "Bearer test-api-token"}


async def test_admin_change_reauth_hash_restart_and_cookie_revocation(client, gateway):
    first = await client.post(
        "/api/v1/auth/login", json={"username": "admin", "password": "test-admin-password"}
    )
    assert first.status_code == 200
    old_cookies = dict(client.cookies)
    csrf = first.json()["csrf"]
    second = httpx.AsyncClient(transport=client._transport, base_url="http://testserver")
    try:
        other = await second.post(
            "/api/v1/auth/login", json={"username": "admin", "password": "test-admin-password"}
        )
        assert other.status_code == 200
        body = {
            "scope": "admin",
            "username": "owner",
            "current_password": "wrong-password",
            "new_password": "new-admin-password",
        }
        wrong = await client.patch("/api/v1/settings/credentials", headers={"X-CSRF-Token": csrf}, json=body)
        assert wrong.status_code == 403
        assert not gateway.credentials.path.exists()
        body["current_password"] = "test-admin-password"
        assert (await client.patch("/api/v1/settings/credentials", json=body)).status_code == 403
        changed = await client.patch(
            "/api/v1/settings/credentials", headers={"X-CSRF-Token": csrf}, json=body
        )
        assert changed.status_code == 200 and changed.json()["requires_login"]
        assert (await second.get("/api/v1/auth/me")).status_code == 401
        client.cookies.update(old_cookies)
        assert (await client.get("/api/v1/auth/me")).status_code == 401
        assert (await client.get("/api/v1/settings/credentials", headers=HEADERS)).status_code == 200
        raw = gateway.credentials.path.read_text()
        assert "new-admin-password" not in raw and "test-admin-password" not in raw
        assert stat.S_IMODE(gateway.credentials.path.stat().st_mode) == 0o600
        assert "new-admin-password" not in json.dumps(gateway._snapshot())
        assert (
            await client.post(
                "/api/v1/auth/login", json={"username": "admin", "password": "test-admin-password"}
            )
        ).status_code == 401
        fresh_config = Config(config_dir=gateway.config.config_dir)
        restored = Credentials(fresh_config)
        assert fresh_config.admin_user == "owner"
        assert await restored.verify("new-admin-password")
        assert not await restored.verify("test-admin-password")
        assert (
            await client.post(
                "/api/v1/auth/login", json={"username": "owner", "password": "new-admin-password"}
            )
        ).status_code == 200
    finally:
        await second.aclose()


async def test_proxy_change_preserves_route_and_supplier_budget(client, gateway):
    binding, node, _ = await gateway.acquire(Route.parse("admin-worker-01-session-fixed"))
    result = await client.patch(
        "/api/v1/settings/credentials",
        headers=HEADERS,
        json={
            "scope": "proxy",
            "username": "rapip",
            "current_password": "test-admin-password",
            "new_password": "new-proxy-password",
        },
    )
    assert result.status_code == 200 and not result.json()["requires_login"]
    assert gateway.config.socks_user == "rapip"
    assert gateway.config.socks_password == "new-proxy-password"
    same, same_node, _ = await gateway.acquire(Route.parse("rapip-worker-01-session-fixed"))
    assert same.id == binding.id and same_node.id == node.id
    assert gateway.test_provider.calls == 0
    restored_config = Config(config_dir=gateway.config.config_dir)
    Credentials(restored_config)
    assert restored_config.socks_user == "rapip" and restored_config.socks_password == "new-proxy-password"
    public = (await client.get("/api/v1/settings/credentials", headers=HEADERS)).text
    assert "password" not in public and "new-proxy-password" not in result.text
    assert "new-proxy-password" not in json.dumps(gateway._snapshot())


async def test_validation_and_reauthentication_are_bounded_and_redacted(client, gateway):
    path = "/api/v1/settings/credentials"
    body = {
        "scope": "proxy",
        "username": "bad-user",
        "current_password": "sensitive-test",
        "new_password": "short-secret",
    }
    invalid = await client.patch(path, headers=HEADERS, json=body)
    assert (
        invalid.status_code == 422
        and "sensitive-test" not in invalid.text
        and "short-secret" not in invalid.text
    )
    assert (await client.patch(path, json={})).status_code == 401
    body.update(username="proxy", new_password="valid-new-password")
    for _ in range(5):
        assert (await client.patch(path, headers=HEADERS, json=body)).status_code == 403
    assert (await client.patch(path, headers=HEADERS, json=body)).status_code == 429
    assert not gateway.credentials.path.exists()


async def test_username_only_rehashes_bootstrap_password(client, gateway):
    response = await client.patch(
        "/api/v1/settings/credentials",
        headers=HEADERS,
        json={
            "scope": "admin",
            "username": "newowner",
            "current_password": "test-admin-password",
        },
    )
    assert response.status_code == 200
    assert await gateway.credentials.verify("test-admin-password")
    assert "test-admin-password" not in gateway.credentials.path.read_text()


async def test_updated_proxy_credentials_reach_http_and_socks_without_rebinding(client, gateway):
    import asyncio
    import base64

    from rapip.http_proxy import HttpProxy
    from rapip.socks import SocksServer

    binding, node, _ = await gateway.acquire(Route.parse("admin-worker-01-session-stable"))
    changed = await client.patch(
        "/api/v1/settings/credentials",
        headers=HEADERS,
        json={
            "scope": "proxy",
            "username": "rapip",
            "current_password": "test-admin-password",
            "new_password": "new-proxy-password",
        },
    )
    assert changed.status_code == 200
    http, socks = HttpProxy(gateway), SocksServer(gateway)
    await http.start()
    await socks.start()
    try:
        for user, password, code in (
            ("admin", "test-socks-password", b"407"),
            ("rapip", "new-proxy-password", b"200"),
        ):
            reader, writer = await asyncio.open_connection(
                "127.0.0.1", http.server.sockets[0].getsockname()[1]
            )
            token = base64.b64encode(f"{user}-worker-01-session-stable:{password}".encode())
            writer.write(
                b"GET http://example.test/ HTTP/1.1\r\nHost: example.test\r\nProxy-Authorization: Basic "
                + token
                + b"\r\n\r\n"
            )
            await writer.drain()
            response = await asyncio.wait_for(reader.read(), 2)
            assert code in response.split(b"\r\n")[0]
            writer.close()
            await writer.wait_closed()
        for user, password, status in (
            ("admin", "test-socks-password", b"\x01\x01"),
            ("rapip", "new-proxy-password", b"\x01\x00"),
        ):
            reader, writer = await asyncio.open_connection(
                "127.0.0.1", socks.server.sockets[0].getsockname()[1]
            )
            writer.write(b"\x05\x01\x02")
            await writer.drain()
            assert await reader.readexactly(2) == b"\x05\x02"
            name = f"{user}-worker-01-session-stable".encode()
            secret = password.encode()
            writer.write(b"\x01" + bytes([len(name)]) + name + bytes([len(secret)]) + secret)
            await writer.drain()
            assert await reader.readexactly(2) == status
            writer.close()
            await writer.wait_closed()
        assert gateway.bindings[binding.id].node_id == node.id
        assert gateway.test_provider.calls == 0
    finally:
        await http.stop()
        await socks.stop()
