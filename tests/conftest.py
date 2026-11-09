from __future__ import annotations

import asyncio
import contextlib
import json
import time

import httpx
import pytest

from rapip.api import create_app
from rapip.config import Config, Settings
from rapip.gateway import Gateway
from rapip.socks import read_address


@pytest.fixture
async def client(gateway):
    app = create_app(gateway.config, gateway, lifecycle=False)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as instance:
        yield instance


class FakeProvider:
    def __init__(self, rows):
        self.rows = rows
        self.calls = 0
        self.uncertain = False

    async def list_nodes(self):
        return [dict(row) for row in self.rows]

    async def usage(self):
        return {
            "checked_at": time.time(),
            "plan_id": "fake-plan",
            "replacement": {"remaining": 100 - self.calls, "total": 100, "used": self.calls},
        }

    async def replace(self, ip, country):
        from rapip.provider import ProviderError

        self.calls += 1
        if self.uncertain:
            raise ProviderError(0, uncertain=True)
        row = next(row for row in self.rows if row["proxy_address"] == ip)
        row["proxy_address"] = f"127.0.1.{self.calls + 1}"
        row["id"] = f"replacement{self.calls}"
        return str(self.calls)

    async def replacement(self, job_id):
        return {"state": "completed", "proxies_added": 1, "proxies_removed": 1}


@pytest.fixture
async def gateway(tmp_path, monkeypatch):
    handlers = set()
    targets = []
    requests = []

    async def upstream(reader, writer):
        task = asyncio.current_task()
        handlers.add(task)
        try:
            header = await reader.readexactly(2)
            await reader.readexactly(header[1])
            writer.write(b"\x05\x02")
            await writer.drain()
            auth = await reader.readexactly(2)
            await reader.readexactly(auth[1])
            length = (await reader.readexactly(1))[0]
            await reader.readexactly(length)
            writer.write(b"\x01\x00")
            await writer.drain()
            request = await reader.readexactly(4)
            target = await read_address(reader, request[3]) + await reader.readexactly(2)
            targets.append(bytes([request[3]]) + target)
            writer.write(b"\x05\x00\x00\x01" + bytes(6))
            await writer.drain()
            first = await reader.read(65536)
            if first.startswith((b"GET ", b"POST ", b"HEAD ")):
                raw = first
                while b"\r\n\r\n" not in raw:
                    raw += await reader.read(65536)
                requests.append(raw)
                body = b"upstream-ok"
                writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 11\r\nConnection: close\r\n\r\n" + body)
                await writer.drain()
            else:
                writer.write(first)
                await writer.drain()
                while data := await reader.read(65536):
                    writer.write(data)
                    await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError, asyncio.CancelledError):
            pass
        finally:
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()
            handlers.discard(task)

    server = await asyncio.start_server(upstream, "0.0.0.0", 0)
    port = server.sockets[0].getsockname()[1]
    connect = asyncio.open_connection

    async def connect_virtual_ip(host, target_port, *args, **kwargs):
        # macOS does not route every 127/8 alias by default. Keep distinct logical IPs,
        # but connect each simulated provider to this one local test listener.
        if target_port == port and host.startswith("127."):
            host = "127.0.0.1"
        return await connect(host, target_port, *args, **kwargs)

    monkeypatch.setattr(asyncio, "open_connection", connect_virtual_ip)
    rows = [
        {
            "id": str(i),
            "proxy_address": f"127.0.0.{i + 2}",
            "port": port,
            "username": "upstream-user",
            "password": "upstream-secret",
            "country_code": "US",
            "valid": True,
        }
        for i in range(6)
    ]
    provider = FakeProvider(rows)
    config = Config(
        data_dir=tmp_path / "data",
        config_dir=tmp_path / "config",
        socks_port=0,
        proxy_http_port=0,
        admin_password="test-admin-password",
        admin_token="test-api-token",
        socks_password="test-socks-password",
        settings=Settings(pool_wait_timeout=0.05),
    )
    config.config_dir.mkdir()
    (config.config_dir / "accounts.json").write_text(
        json.dumps([{"account_id": "test", "replacement_budget": 20}])
    )

    async def probe(node, timeout):
        return True

    instance = Gateway(config, provider_factory=lambda *args: provider, probe=probe)
    await instance.start(background=False)
    instance.test_provider = provider
    instance.test_targets = targets
    instance.test_requests = requests
    yield instance
    await instance.stop()
    server.close()
    await server.wait_closed()
    for task in list(handlers):
        task.cancel()
    if handlers:
        await asyncio.gather(*handlers, return_exceptions=True)
