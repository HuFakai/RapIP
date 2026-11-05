import asyncio
import base64
import json
import time
from dataclasses import asdict
from urllib.parse import urlparse

import pytest

from rapip.gateway import Gateway
from rapip.http_proxy import HttpProxy
from rapip.models import GatewayError, IPState, Route
from rapip.socks import SocksServer, address_bytes

HEADERS = {"Authorization": "Bearer test-api-token"}


async def test_row_reserves_exact_ip_and_never_spends_on_cancel(gateway):
    node = list(gateway.nodes.values())[3]
    copies = await asyncio.gather(*(gateway.workers.issue(node_id=node.id) for _ in range(10)))
    assert len({(r.worker, r.session) for r, _ in copies}) == 1
    assert len(gateway.bindings) == 1 and node.state == IPState.RESERVED and not node.used
    assert gateway.stats()["fresh_count"] == 5 and gateway.stats()["reserved_count"] == 1
    other = await gateway.acquire(Route("admin", "other", "other"))
    assert other[1].id != node.id
    binding = gateway.bindings[node.binding_id]
    await gateway.release(binding.id, reservation_only=True)
    assert node.state == IPState.FRESH and node.binding_id is None and not node.used
    assert node.ip not in gateway.blacklist and not gateway.replacements
    with pytest.raises(GatewayError, match="失效"):
        await gateway.acquire(copies[0][0])
    assert gateway.test_provider.calls == 0


async def test_reserved_first_connect_activates_and_cancel_race_cannot_replace(gateway):
    node = next(iter(gateway.nodes.values()))
    route, _ = await gateway.workers.issue(node_id=node.id)
    results = await asyncio.gather(*(gateway.acquire(route) for _ in range(10)))
    assert all(n.id == node.id for _, n, _ in results)
    binding = results[0][0]
    assert node.used and not binding.reserved and node.state == IPState.IN_USE
    with pytest.raises(GatewayError, match="已被连接使用"):
        await gateway.release(binding.id, reservation_only=True)
    assert not binding.released and not gateway.replacements and not gateway.blacklist
    assert (await gateway.workers.issue(node_id=node.id))[0] == route


async def test_normal_link_is_lazy_reused_until_consumed_then_exhausts(gateway):
    routes = []
    for _ in range(6):
        before = gateway.stats()["fresh_count"]
        route, reserved = await gateway.workers.issue()
        assert not reserved and gateway.stats()["fresh_count"] == before
        assert (await gateway.workers.issue())[0] == route
        routes.append(route)
        await gateway.acquire(route)
    assert len({r.worker for r in routes}) == 6
    with pytest.raises(GatewayError, match="无待用链接"):
        await gateway.workers.issue()
    assert gateway.test_provider.calls == 0


async def test_used_copy_keeps_country_session_and_anonymous_identity(gateway):
    for route in [Route("admin", "country", "custom", "US"), Route("admin", "anonymous")]:
        binding, node, _ = await gateway.acquire(route)
        result, reserved = await gateway.workers.issue(node_id=node.id)
        assert result == route and not reserved and gateway.bindings[binding.id].generation == 1


async def test_new_session_on_unused_reservation_frees_old_without_replace(gateway):
    node = next(iter(gateway.nodes.values()))
    route, _ = await gateway.workers.issue(node_id=node.id)
    new = Route(**{**asdict(route), "session": "next"})
    binding, new_node, _ = await gateway.acquire(new)
    assert new_node.id != node.id and node.state == IPState.FRESH and not node.binding_id
    assert not gateway.blacklist and not gateway.replacements and not binding.reserved


async def test_reserved_and_pending_survive_restart_and_do_not_idle_expire(gateway):
    node = next(iter(gateway.nodes.values()))
    route, _ = await gateway.workers.issue(node_id=node.id)
    pending, _ = await gateway.workers.issue()
    binding_id = node.binding_id
    gateway.bindings[binding_id].last_detached = time.time() - 864000
    await gateway.maintain()
    assert node.state == IPState.RESERVED
    config, provider, probe = gateway.config, gateway.test_provider, gateway.probe
    await gateway.stop()
    restarted = Gateway(config, provider_factory=lambda *args: provider, probe=probe)
    await restarted.start(background=False)
    try:
        assert restarted.public_binding(restarted.bindings[binding_id])["status"] == "RESERVED"
        assert (await restarted.workers.issue())[0] == pending
        assert (await restarted.acquire(route))[1].id == node.id
        assert provider.calls == 0
    finally:
        await restarted.stop()
        # Fixture owns the original object and will stop it again.
        gateway.stopping = False
        await gateway.start(background=False)


async def test_worker_rules_validate_new_routes_but_grandfather_existing(gateway):
    legacy, _, _ = await gateway.acquire(Route("admin", "old", "custom"))
    anonymous, _, _ = await gateway.acquire(Route("admin", "anon"))
    await gateway.workers.save("default", True)
    assert (await gateway.acquire(Route("admin", "old", "custom")))[0] == legacy
    assert (await gateway.acquire(Route("admin", "anon")))[0] == anonymous
    for worker in [None, "custom2", "7", "07", "1", "00"]:
        with pytest.raises(GatewayError, match="工位"):
            await gateway.acquire(Route("admin", "new", worker))
    await gateway.acquire(Route("admin", "new", "03"))
    before = gateway.workers.capacity("default")
    for node in gateway.nodes.values():
        if node.state == IPState.FRESH:
            node.state = IPState.ARCHIVED
    assert gateway.workers.capacity("default") == before


async def test_proxy_link_api_auth_csrf_secrets_idempotency(client, gateway):
    path = "/api/v1/proxy-links"
    node = next(iter(gateway.nodes.values()))
    assert (await client.post(path, json={})).status_code == 401
    headers = {**HEADERS, "Idempotency-Key": "copy-first-test"}
    response = await client.post(path, json={"node_id": node.id}, headers=headers)
    assert response.status_code == 200
    data = response.json()
    assert urlparse(data["links"]["http"]).username == data["username"]
    assert "test-socks-password" in data["links"]["socks5"]
    assert "upstream-secret" not in response.text
    assert (await client.post(path, json={"node_id": node.id}, headers=headers)).json()["username"] == data[
        "username"
    ]
    assert (await client.post(path, json={}, headers=headers)).status_code == 409
    assert "test-socks-password" not in json.dumps(gateway._snapshot())
    assert not gateway.operations
    login = await client.post(
        "/api/v1/auth/login", json={"username": "admin", "password": "test-admin-password"}
    )
    assert login.status_code == 200
    assert (await client.post(path, json={})).status_code == 403


async def test_generated_reservation_connects_over_http_with_exact_node(gateway):
    node = list(gateway.nodes.values())[4]
    route, _ = await gateway.workers.issue(node_id=node.id)
    server = HttpProxy(gateway)
    socks = SocksServer(gateway)
    await server.start()
    await socks.start()
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", server.server.sockets[0].getsockname()[1])
        username = f"admin-worker-{route.worker}-session-{route.session}"
        token = base64.b64encode(f"{username}:test-socks-password".encode())
        writer.write(
            b"CONNECT target.invalid:443 HTTP/1.1\r\nHost: target.invalid:443\r\nProxy-Authorization: Basic "
            + token
            + b"\r\n\r\n"
        )
        await writer.drain()
        assert b"200" in await reader.readuntil(b"\r\n\r\n")
        writer.write(b"reserved-exit")
        await writer.drain()
        assert await asyncio.wait_for(reader.readexactly(13), 2) == b"reserved-exit"
        assert gateway.binding_for(route.slot).node_id == node.id
        writer.close()
        await writer.wait_closed()
        reader, writer = await asyncio.open_connection("127.0.0.1", socks.server.sockets[0].getsockname()[1])
        writer.write(b"\x05\x01\x02")
        await writer.drain()
        assert await reader.readexactly(2) == b"\x05\x02"
        user, password = username.encode(), b"test-socks-password"
        writer.write(b"\x01" + bytes([len(user)]) + user + bytes([len(password)]) + password)
        await writer.drain()
        assert await reader.readexactly(2) == b"\x01\x00"
        writer.write(b"\x05\x01\x00" + address_bytes("target.invalid", 443))
        await writer.drain()
        assert (await reader.readexactly(10))[:2] == b"\x05\x00"
        writer.write(b"same-exit")
        await writer.drain()
        assert await asyncio.wait_for(reader.readexactly(9), 2) == b"same-exit"
        assert gateway.stats()["in_use_count"] == 1
        assert gateway.binding_for(route.slot).node_id == node.id
        writer.close()
        await writer.wait_closed()
    finally:
        await server.stop()
        await socks.stop()


async def test_row_and_normal_race_never_share_ip_or_bind_twice(gateway):
    node = next(iter(gateway.nodes.values()))
    ordinary, _ = await gateway.workers.issue()
    pinned, _ = await gateway.workers.issue(node_id=node.id)
    results = await asyncio.gather(gateway.acquire(ordinary), gateway.acquire(pinned))
    # A pending identity may be reused by explicit row reservation; it stays one binding.
    assert len({b.id for b, _, _ in results}) == len({n.id for _, n, _ in results})
    assert (await gateway.workers.issue())[0].worker != pinned.worker
