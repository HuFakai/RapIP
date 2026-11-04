import asyncio
import base64

from rapip.http_proxy import HttpProxy
from rapip.socks import SocksServer, address_bytes


async def socks_client(port, session="aaa"):
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(b"\x05\x01\x02")
    await writer.drain()
    assert await reader.readexactly(2) == b"\x05\x02"
    user, password = f"admin-worker-01-session-{session}".encode(), b"test-socks-password"
    writer.write(b"\x01" + bytes([len(user)]) + user + bytes([len(password)]) + password)
    await writer.drain()
    assert await reader.readexactly(2) == b"\x01\x00"
    writer.write(b"\x05\x01\x00" + address_bytes("must-not-resolve.invalid", 443))
    await writer.drain()
    assert (await reader.readexactly(10))[:2] == b"\x05\x00"
    return reader, writer


async def test_socks_forwards_domain_without_local_dns_and_preserves_long_connection(gateway):
    server = SocksServer(gateway)
    await server.start()
    port = server.server.sockets[0].getsockname()[1]
    try:
        reader, writer = await socks_client(port)
        writer.write(b"hello")
        await writer.drain()
        assert await asyncio.wait_for(reader.readexactly(5), 2) == b"hello"
        assert gateway.test_targets[0][0] == 3
        gateway.settings.session_idle_timeout = 0.01
        await asyncio.sleep(0.03)
        await gateway.maintain()
        writer.write(b"still-connected")
        await writer.drain()
        assert await asyncio.wait_for(reader.readexactly(15), 2) == b"still-connected"
        writer.close()
        await writer.wait_closed()
    finally:
        await server.stop()


async def test_http_connect_shares_socks_binding(gateway):
    socks = SocksServer(gateway)
    http = HttpProxy(gateway)
    await socks.start()
    await http.start()
    try:
        _, socks_writer = await socks_client(socks.server.sockets[0].getsockname()[1])
        reader, writer = await asyncio.open_connection("127.0.0.1", http.server.sockets[0].getsockname()[1])
        token = base64.b64encode(b"admin-worker-01-session-aaa:test-socks-password")
        writer.write(
            b"CONNECT must-not-resolve.invalid:443 HTTP/1.1\r\nHost: must-not-resolve.invalid:443\r\nProxy-Authorization: Basic "
            + token
            + b"\r\n\r\n"
        )
        await writer.drain()
        assert (await reader.readuntil(b"\r\n\r\n")).startswith(b"HTTP/1.1 200")
        writer.write(b"https-tunnel")
        await writer.drain()
        assert await asyncio.wait_for(reader.readexactly(12), 2) == b"https-tunnel"
        assert gateway.stats()["in_use_count"] == 1
        writer.close()
        socks_writer.close()
        await writer.wait_closed()
        await socks_writer.wait_closed()
    finally:
        await socks.stop()
        await http.stop()


async def test_http_forwarding_strips_proxy_auth(gateway):
    server = HttpProxy(gateway)
    await server.start()
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", server.server.sockets[0].getsockname()[1])
        token = base64.b64encode(b"admin-worker-01-session-aaa:test-socks-password")
        writer.write(
            b"GET http://must-not-resolve.invalid/test?q=1 HTTP/1.1\r\nHost: must-not-resolve.invalid\r\nProxy-Authorization: Basic "
            + token
            + b"\r\n\r\n"
        )
        await writer.drain()
        result = await asyncio.wait_for(reader.read(), 2)
        assert b"200 OK" in result and b"upstream-ok" in result
        request = gateway.test_requests[-1]
        assert request.startswith(b"GET /test?q=1 HTTP/1.1")
        assert b"proxy-authorization" not in request.lower()
        writer.close()
        await writer.wait_closed()
    finally:
        await server.stop()


async def test_http_requires_authentication(gateway):
    server = HttpProxy(gateway)
    await server.start()
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", server.server.sockets[0].getsockname()[1])
        writer.write(b"CONNECT example.com:443 HTTP/1.1\r\nHost: example.com\r\n\r\n")
        await writer.drain()
        assert b"407" in await asyncio.wait_for(reader.read(), 2)
        assert not gateway.bindings
        writer.close()
        await writer.wait_closed()
    finally:
        await server.stop()
