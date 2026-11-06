import asyncio
import time
from dataclasses import replace

import pytest

from rapip.dynamic import DynamicPolicy
from rapip.gateway import Gateway
from rapip.models import GatewayError, IPState, Route


def route(session, worker="01", country=None):
    return Route("admin", session, worker, country)


def grant(gateway):
    gateway.supplier_usage["test"] = {
        "checked_at": time.time(),
        "plan_id": "1",
        "period_start": "2026-01-01T00:00:00+00:00",
        "replacement": {"remaining": 40, "total": 40, "used": 0},
    }


async def until(predicate):
    async with asyncio.timeout(2):
        while not predicate():
            await asyncio.sleep(0.005)


async def switch_task(gateway, session):
    request = asyncio.create_task(gateway.acquire(route(session)))
    await until(
        lambda: request.done() or any(p["session"] == session for p in gateway.dynamic.pending.values())
    )
    return request


async def test_pool_first_keeps_fast_pool_path_then_replaces_in_place_without_stock(gateway):
    grant(gateway)
    await gateway.dynamic.save("default", DynamicPolicy(mode="pool_then_in_place"))
    binding, old, _ = await gateway.acquire(route("old"))
    _, middle, _ = await gateway.acquire(route("pool"))
    assert middle.id != old.id and gateway.test_provider.calls == 0
    assert next(iter(gateway.replacements.values())).cause == "client_session"
    for node in gateway.nodes.values():
        if node.state == IPState.FRESH:
            node.state = IPState.QUARANTINED
    refill = next(iter(gateway.replacements.values()))
    await gateway.process_replacement(refill.id)
    for node in gateway.nodes.values():
        if node.state == IPState.FRESH:
            node.state = IPState.QUARANTINED
    task = await switch_task(gateway, "inplace")
    assert len(gateway.dynamic.pending) == 1
    job = gateway.replacements[gateway.dynamic.pending[binding.id]["job_id"]]
    assert job.node_id == middle.id
    await gateway.process_replacement(job.id)
    assert (await task)[1].ip == job.new_ip
    assert gateway.test_provider.calls == 2


async def test_queued_processing_admission_share_single_real_submit_and_commit_latest(gateway):
    grant(gateway)
    await gateway.dynamic.save("default", DynamicPolicy(mode="in_place"))
    binding, old, _ = await gateway.acquire(route("original"))
    fresh_pool = {n.id for n in gateway.nodes.values() if n.state == IPState.FRESH}
    provider = gateway.test_provider
    completed = False
    original_status = provider.replacement
    original_probe = gateway.probe
    check_entered = asyncio.Event()
    allow_check = asyncio.Event()

    async def status(job_id):
        return await original_status(job_id) if completed else {"state": "processing"}

    async def probe(node, timeout):
        if node.ip.startswith("127.0.1."):
            check_entered.set()
            await allow_check.wait()
        return await original_probe(node, timeout)

    provider.replacement, gateway.probe = status, probe
    b = await switch_task(gateway, "candidateB")
    job = next(iter(gateway.replacements.values()))
    await gateway.process_replacement(job.id)
    assert job.state == "PROCESSING" and provider.calls == 1

    c = await switch_task(gateway, "candidateC")
    for prior in (b, asyncio.create_task(gateway.acquire(route("candidateB")))):
        with pytest.raises(GatewayError) as retired:
            await prior
        assert retired.value.code == "SESSION_RETIRED"

    completed = True
    supplier = asyncio.create_task(gateway.process_replacement(job.id))
    await asyncio.wait_for(check_entered.wait(), 2)
    assert job.state == "COMPLETED" and binding.session == "original"
    d = await switch_task(gateway, "candidateD")
    with pytest.raises(GatewayError, match="候选"):
        await c
    with pytest.raises(GatewayError) as held:
        await gateway.rotate(binding.id)
    assert held.value.code == "IN_PLACE_BUSY"
    assert provider.calls == 1
    allow_check.set()
    await supplier
    actual, node, gen = await d
    assert actual.session == "candidateD" and node.ip == job.new_ip and gen == 2
    assert {n.id for n in gateway.nodes.values() if n.state == IPState.FRESH} == fresh_pool
    assert (await gateway.acquire(route("candidateD")))[1].id == node.id
    with pytest.raises(GatewayError) as cooldown:
        await gateway.acquire(route("candidateE"))
    assert cooldown.value.code == "IN_PLACE_COOLDOWN"
    assert old.state == IPState.ARCHIVED and provider.calls == 1
    chains, _ = await gateway.storage.chains("links", {"binding_id": binding.id}, 0, 10, 0)
    chain = chains[gateway.lifecycle.key(binding.id, gen)]
    assert {e["kind"] for e in chain} >= {
        "in_place_started",
        "in_place_join",
        "in_place_admission",
        "webshare",
        "switched",
    }
    assert {e.get("job_id") for e in chain} == {job.id}
    gateway.dynamic.last_commit[binding.id] -= 61
    next_request = await switch_task(gateway, "candidateE")
    assert gateway.dynamic.pending[binding.id]["job_id"] != job.id and provider.calls == 1
    next_request.cancel()
    with pytest.raises(asyncio.CancelledError):
        await next_request


async def test_cancelled_client_and_restart_do_not_repeat_submit(gateway, tmp_path):
    grant(gateway)
    await gateway.dynamic.save("default", DynamicPolicy(mode="in_place"))
    binding, _, _ = await gateway.acquire(route("old"))
    task = await switch_task(gateway, "new")
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    job = next(iter(gateway.replacements.values()))
    gateway.test_provider.uncertain = True
    await gateway.process_replacement(job.id)
    assert job.state == "UNCERTAIN"
    import shutil

    restored_dir = tmp_path / "restored"
    async with gateway.changed:
        await gateway.storage.db.execute("PRAGMA wal_checkpoint(FULL)")
        restored_dir.mkdir()
        shutil.copy2(gateway.config.data_dir / "gateway.sqlite3", restored_dir)
    restored = Gateway(
        replace(gateway.config, data_dir=restored_dir),
        provider_factory=lambda *args: gateway.test_provider,
        probe=gateway.probe,
    )
    await restored.start(background=False, sync=False)
    try:
        with pytest.raises(GatewayError) as review:
            await restored.acquire(route("newer"))
        assert review.value.code == "IN_PLACE_REVIEW"
        assert restored.dynamic.pending[binding.id]["job_id"] == job.id
        assert gateway.test_provider.calls == 1
    finally:
        await restored.stop()


async def test_admission_failure_never_returns_bad_exit_or_spends_again(gateway):
    grant(gateway)
    await gateway.dynamic.save("default", DynamicPolicy(mode="in_place"))
    binding, _, _ = await gateway.acquire(route("old"))

    async def fail_new(node, timeout):
        return not node.ip.startswith("127.0.1.")

    gateway.probe = fail_new
    task = await switch_task(gateway, "badcandidate")
    job = next(iter(gateway.replacements.values()))
    await gateway.process_replacement(job.id)
    with pytest.raises(GatewayError) as review:
        await task
    assert review.value.code == "IN_PLACE_REVIEW"
    node = gateway.nodes[binding.node_id]
    assert node.state == IPState.QUARANTINED and node.binding_id == binding.id
    with pytest.raises(GatewayError) as review:
        await gateway.acquire(route("afterFailure"))
    assert review.value.code == "IN_PLACE_REVIEW"
    assert gateway.test_provider.calls == 1
    await gateway.release(binding.id, "hard")
    assert node.state == IPState.QUARANTINED and not node.binding_id


async def test_explicit_preflight_rejection_restores_old_binding_without_charge(gateway):
    grant(gateway)
    await gateway.dynamic.save("default", DynamicPolicy(mode="in_place"))
    gateway.replenishment["default"] = {"mode": "country", "country": "US", "fallback": False}

    async def rejected(ip, selection, dry_run=False):
        assert dry_run and selection["country_code"] == "US"
        return {"state": "failed", "error_code": "proxies_removed_doesnt_match_added"}

    gateway.test_provider.replace_selected = rejected
    binding, old, _ = await gateway.acquire(route("old"))
    task = await switch_task(gateway, "declined")
    job = next(iter(gateway.replacements.values()))
    await gateway.process_replacement(job.id)
    with pytest.raises(GatewayError):
        await task
    assert job.state == "FAILED" and job.attempts == 0 and gateway.test_provider.calls == 0
    assert (await gateway.acquire(route("old")))[1].id == old.id
    assert binding.id not in gateway.dynamic.pending


async def test_chain_filter_keeps_whole_pool_switch_and_recent_chain_first(gateway):
    binding, _, _ = await gateway.acquire(route("old"))
    _, _, _ = await gateway.acquire(route("new"))
    job = next(iter(gateway.replacements.values()))
    await gateway.process_replacement(job.id)
    chains, total = await gateway.storage.chains("links", {"binding_id": binding.id}, 0, 10, 0)
    assert total == 2 and list(chains)[0] == gateway.lifecycle.key(binding.id, 2)
    filtered, _ = await gateway.storage.chains("ips", {"job_id": job.id}, 0, 10, 0)
    events = next(iter(filtered.values()))
    assert "switched" in {e["kind"] for e in events}
    assert "webshare" in {e["kind"] for e in events}


async def test_real_proxy_handshakes_wait_for_same_job_and_reopen_on_new_ip(gateway):
    import base64

    from test_protocols import socks_client

    from rapip.http_proxy import HttpProxy
    from rapip.socks import SocksServer

    grant(gateway)
    await gateway.dynamic.save("default", DynamicPolicy(mode="in_place"))
    socks, http = SocksServer(gateway), HttpProxy(gateway)
    await socks.start()
    await http.start()
    try:
        socks_port = socks.server.sockets[0].getsockname()[1]
        old_reader, old_writer = await socks_client(socks_port, "old")
        reader, writer = await asyncio.open_connection("127.0.0.1", http.server.sockets[0].getsockname()[1])
        token = base64.b64encode(b"admin-worker-01-session-new:test-socks-password")
        writer.write(
            b"CONNECT target.invalid:443 HTTP/1.1\r\nHost: target.invalid:443\r\n"
            b"Proxy-Authorization: Basic " + token + b"\r\n\r\n"
        )
        await writer.drain()
        await until(lambda: bool(gateway.dynamic.pending))
        second = asyncio.create_task(socks_client(socks_port, "new"))
        await until(lambda: gateway.waiters == 2)
        assert not second.done() and len(gateway.replacements) == 1
        job = next(iter(gateway.replacements.values()))
        await gateway.process_replacement(job.id)
        assert (await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 2)).startswith(b"HTTP/1.1 200")
        new_reader, new_writer = await asyncio.wait_for(second, 2)
        assert await asyncio.wait_for(old_reader.read(1), 2) == b""
        new_writer.write(b"new exit")
        await new_writer.drain()
        assert await asyncio.wait_for(new_reader.readexactly(8), 2) == b"new exit"
        assert (
            gateway.test_provider.calls == 1
            and len({t["node_id"] for t in gateway.tunnels.values() if t["node_id"] != job.node_id}) == 1
        )
        for w in (old_writer, writer, new_writer):
            w.close()
            await w.wait_closed()
    finally:
        await socks.stop()
        await http.stop()


async def test_quality_group_requires_pass_and_preserves_explicit_country(gateway, monkeypatch):
    from test_quality import create_group

    grant(gateway)
    ids = await create_group(gateway, count=1, daily_limit=5, round_limit=5, cooldown_seconds=0)

    async def network(node, policy):
        return {
            "complete": True,
            "successes": policy.samples,
            "samples": policy.samples,
            "max_latency_ms": 100,
            "exits": [node.ip],
            "exit_match": True,
        }

    monkeypatch.setattr(gateway.quality, "network_check", network)
    await gateway.quality.run("register", ids)
    await gateway.dynamic.save("register", DynamicPolicy(mode="in_place"))
    binding, old, _ = await gateway.acquire(Route("admin", "old", "01", "US", "register"))
    calls = []

    async def selected(ip, rule, dry_run=False):
        assert rule == {"type": "country", "country_code": "US", "count": 1}
        calls.append(dry_run)
        if dry_run:
            return {"id": 100, "state": "validated", "proxies_added": 1, "proxies_removed": 1}
        return {"id": await gateway.test_provider.replace(ip, "US")}

    gateway.test_provider.replace_selected = selected
    task = asyncio.create_task(gateway.acquire(Route("admin", "new", "01", "US", "register")))
    await until(lambda: bool(gateway.dynamic.pending))
    job = next(iter(gateway.replacements.values()))
    await gateway.process_replacement(job.id)
    _, node, _ = await task
    assert calls == [True, False] and gateway.test_provider.calls == 1
    assert binding.session == "new" and node.slot_id == old.slot_id and node.group_id == "register"
    assert gateway.quality.state(node) == "PASS"
    assert gateway.quality.ledger[-1]["cause"] == "client_in_place"
