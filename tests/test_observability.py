import time

HEADERS = {"Authorization": "Bearer test-api-token"}


async def test_metrics_survive_reload_and_have_real_deltas(gateway):
    obs = gateway.observer
    obs.add("up", 100, "test", "default")
    obs.add("attempts", 2, "test", "default")
    obs.add("successes", 1, "test", "default")
    await obs.flush(gateway)
    data = await obs.metrics()
    assert data["summary"]["up"] == 100 and data["summary"]["attempts"] == 2
    await obs.flush(gateway)
    assert (await obs.metrics())["summary"]["up"] == 100
    from rapip.observability import Observability

    other = Observability(gateway.config.data_dir)
    await other.open()
    try:
        assert (await other.metrics())["summary"]["up"] == 100
    finally:
        await other.close()


async def test_metadata_recording_modes_and_bounded_queue(gateway):
    obs = gateway.observer
    one = obs.begin("socks5")
    one.update(result="success")
    obs.finish(one)
    assert not [r for kind, r in obs.queue if kind == "connection"]
    two = obs.begin("http")
    two["target"] = "example.com:443"
    obs.finish(two, "authentication_rejected")
    obs.finish(two, "authentication_rejected")
    await obs.flush(gateway)
    rows = await obs.records("connection", {})
    assert rows["total"] == 1 and rows["items"][0]["target"] == "example.com:443"
    assert "_counted" not in rows["items"][0]
    assert (await obs.metrics())["summary"]["attempts"] == 2
    obs.mode = "off"
    obs.finish(obs.begin("http"), "failed")
    assert not [r for kind, r in obs.queue if kind == "connection"]
    for i in range(2010):
        obs.enqueue("probe", {"id": str(i), "at": time.time()})
    assert len(obs.queue) == 2000 and obs.dropped >= 10


async def test_observability_error_does_not_block_business(gateway, monkeypatch):
    async def fail(*args, **kwargs):
        raise RuntimeError("disk error")

    monkeypatch.setattr(gateway.observer.db, "execute", fail)
    await gateway.observer.flush(gateway)
    assert gateway.observer.error and not gateway.storage_failed
    from rapip.models import Route

    binding, _, _ = await gateway.acquire(Route.parse("admin-session-alive"))
    assert not binding.released


async def test_v2_endpoints_authentication_and_source_key_non_disclosure(client, gateway):
    for path in ("/groups", "/overview", "/metrics", "/connections", "/probes", "/quality/sources"):
        assert (await client.get("/api/v1" + path)).status_code == 401
        response = await client.get("/api/v1" + path, headers=HEADERS)
        assert response.status_code == 200, response.text
    response = await client.patch(
        "/api/v1/quality/sources/ipinfo",
        headers=HEADERS,
        json={"key": "top-secret-test-key", "daily_limit": 5},
    )
    assert response.status_code == 200 and "top-secret-test-key" not in response.text
    assert (gateway.config.config_dir / "quality_sources.json").stat().st_mode & 0o777 == 0o600
    invalid = await client.patch(
        "/api/v1/quality/sources/ipinfo", headers=HEADERS, json={"key": "secret", "daily_limit": -1}
    )
    assert invalid.status_code == 422 and "secret" not in invalid.text


async def test_nodes_cidr_filters_and_sort(client, gateway):
    response = await client.get(
        "/api/v1/nodes?q=127.0.0.0/24&sort=ip&direction=desc&page_size=2", headers=HEADERS
    )
    assert response.status_code == 200 and response.json()["total"] == 6
    assert response.json()["items"][0]["ip"] == "127.0.0.7"
    assert (await client.get("/api/v1/nodes?q=bad/24", headers=HEADERS)).status_code == 422


async def test_compaction_preserves_totals(gateway):
    o = gateway.observer
    await o.db.execute(
        "INSERT INTO samples VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (int(time.time() - 90000) // 10 * 10, 10, "global", "", 123, 456, 2, 1, 1, 3, 2, 5, 4, "{}", "old"),
    )
    await o.db.commit()
    async with o.lock:
        await o.compact(time.time())
    r = await o.metrics("7d")
    assert r["summary"]["up"] == 123 and r["summary"]["successes"] == 1


async def test_protocol_metadata_never_records_auth_or_url_query(gateway):
    import asyncio
    import base64
    import json

    from rapip.http_proxy import HttpProxy

    gateway.observer.mode = "all"
    server = HttpProxy(gateway)
    await server.start()
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", server.server.sockets[0].getsockname()[1])
        token = base64.b64encode(b"admin-worker-01-session-first:test-socks-password")
        writer.write(
            b"GET http://example.test/path?secret=private HTTP/1.1\r\nHost: example.test\r\nProxy-Authorization: Basic "
            + token
            + b"\r\nCookie: hidden-cookie\r\n\r\n"
        )
        await writer.drain()
        await reader.read()
        writer.close()
        await writer.wait_closed()
        await asyncio.sleep(0.02)
        await gateway.observer.flush(gateway)
        records = await gateway.observer.records("connection", {})
        assert records["total"] == 1
        serialized = json.dumps(records)
        assert all(
            v not in serialized
            for v in ("test-socks-password", "hidden-cookie", "private", "?secret", "Authorization")
        )
        assert records["items"][0]["target"] == "example.test:80"
        metrics = await gateway.observer.metrics()
        assert metrics["summary"]["attempts"] == 1 and metrics["summary"]["successes"] == 1
        combined = await gateway.observer.metrics(account="test", group="default")
        assert combined["summary"]["attempts"] == 1
    finally:
        await server.stop()


async def test_ten_thousand_nodes_are_paginated_not_downloaded(client, gateway):
    import ipaddress
    from dataclasses import replace

    template = next(iter(gateway.nodes.values()))
    gateway.nodes = {}
    for i in range(10000):
        node = replace(template, id=f"scale{i}", ip=str(ipaddress.ip_address(0xC6120000 + i)), slot_id="")
        gateway.nodes[node.id] = node
        gateway.quality.ensure_node(node)
    response = await client.get("/api/v1/nodes?page_size=20&page=100", headers=HEADERS)
    assert response.status_code == 200 and response.json()["total"] == 10000
    assert len(response.json()["items"]) == 20 and len(response.content) < 100000
    overview = await client.get("/api/v1/overview", headers=HEADERS)
    assert overview.status_code == 200 and overview.json()["stats"]["total_ips"] == 10000


async def test_restart_and_missing_samples_are_exposed_as_gaps(gateway):
    o = gateway.observer
    now = int(time.time() // 30) * 30
    for timestamp, instance in ((now - 180, "old"), (now - 30, "new")):
        await o.db.execute(
            "INSERT INTO samples VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (timestamp, 10, "global", "", 10, 20, 1, 1, 0, 0, 0, 10, 1, "{}", instance),
        )
    await o.db.commit()
    data = await o.metrics("1h")
    assert any(p["instance"] == "missing" and p["gap"] for p in data["points"])
    assert next(p for p in data["points"] if p["instance"] == "new")["gap"]
    assert data["summary"]["up"] == 20
