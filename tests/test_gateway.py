import asyncio
import json
import time

import pytest

from rapip.gateway import Gateway
from rapip.models import GatewayError, IPState, Route


def route(worker="01", session="aaa", country=None):
    return Route("admin", session, worker, country)


@pytest.mark.parametrize(
    "username",
    [
        "admin-worker-01-session-aaa",
        "admin_worker_01_session_aaa",
        "admin-country-us-worker-01-session-aaa",
        "admin-session-aaa",
    ],
)
def test_route_forms(username):
    parsed = Route.parse(username)
    assert parsed.user == "admin"
    assert parsed.session == "aaa"


@pytest.mark.parametrize(
    "username",
    [
        "admin-worker-01_session-aaa",
        "admin-session-",
        "admin-session-a-b",
        "admin-country-USA-worker-1-session-a",
        "admin-country-US-session-a",
    ],
)
def test_invalid_route(username):
    with pytest.raises(GatewayError):
        Route.parse(username)


async def test_same_session_concurrent_requests_share_one_ip(gateway):
    results = await asyncio.gather(*(gateway.acquire(route()) for _ in range(20)))
    assert len({node.id for _, node, _ in results}) == 1
    assert gateway.stats()["in_use_count"] == 1


async def test_different_workers_never_share_ip(gateway):
    gateway.settings.max_active_bindings = 3
    results = await asyncio.gather(*(gateway.acquire(route(str(i))) for i in range(3)))
    assert len({node.ip for _, node, _ in results}) == 3
    with pytest.raises(GatewayError):
        await gateway.acquire(route("04"))
    assert gateway.stats()["active_bindings"] == 3


async def test_rotate_retires_old_session(gateway):
    binding, old, _ = await gateway.acquire(route())
    _, fresh, _ = await gateway.acquire(route(session="bbb"))
    assert old.ip != fresh.ip
    assert old.state == IPState.REPLACING
    assert gateway.blacklist[old.ip] > time.time()
    with pytest.raises(GatewayError, match="失效"):
        await gateway.acquire(route())
    assert binding.session == "bbb"


async def test_manual_rotation_preserves_session_and_checks_generation(gateway):
    binding, old, generation = await gateway.acquire(route())
    await gateway.rotate(binding.id, expected=generation)
    assert binding.session == "aaa"
    assert binding.node_id != old.id
    with pytest.raises(GatewayError, match="变化"):
        await gateway.rotate(binding.id, expected=generation)


async def test_country_missing_does_not_reuse_other_country(gateway):
    with pytest.raises(GatewayError):
        await gateway.acquire(route(country="GB"))
    assert gateway.stats()["in_use_count"] == 0


async def test_exhaustion_preserves_old_binding(gateway):
    binding, old, generation = await gateway.acquire(route())
    for node in gateway.nodes.values():
        if node.id != old.id:
            node.state = IPState.QUARANTINED
    with pytest.raises(GatewayError):
        await gateway.rotate(binding.id)
    assert binding.node_id == old.id and binding.generation == generation
    assert old.state == IPState.IN_USE


async def test_anonymous_session_is_sticky(gateway):
    a = await gateway.acquire(Route("admin", "anonymous"))
    b = await gateway.acquire(Route("admin", "anonymous"))
    assert a[0].id == b[0].id


async def test_fault_preserves_session_and_changes_ip(gateway):
    gateway.rotation.policies["default"] = {**gateway.rotation.policy("default"), "fault_failover": True}
    binding, old, _ = await gateway.acquire(route())
    assert not await gateway.connection_failure(old.id, "transport")
    assert await gateway.connection_failure(old.id, "transport")
    again, new, _ = await gateway.acquire(route())
    assert again.id == binding.id and again.session == "aaa" and new.ip != old.ip
    assert binding.failovers == 1


async def test_target_failure_does_not_burn_node(gateway):
    _, node, _ = await gateway.acquire(route())
    assert not await gateway.connection_failure(node.id, "target")
    assert node.state == IPState.IN_USE and not gateway.replacements


async def test_upstream_auth_error_pauses_account_without_burning(gateway):
    _, node, _ = await gateway.acquire(route())
    await gateway.connection_failure(node.id, "authentication")
    assert not gateway.replacements
    assert gateway.account_health["test"]["state"] == "authentication_error"


async def test_replacement_refills_pool_without_reusing_old_ip(gateway):
    binding, old, _ = await gateway.acquire(route())
    await gateway.rotate(binding.id)
    job = next(iter(gateway.replacements.values()))
    await gateway.process_replacement(job.id)
    assert job.state == "COMPLETED"
    assert old.state == IPState.ARCHIVED
    assert gateway.test_provider.calls == 1
    assert gateway.stats()["fresh_count"] == 5


async def test_uncertain_post_is_not_repeated(gateway):
    binding, _, _ = await gateway.acquire(route())
    await gateway.rotate(binding.id)
    job = next(iter(gateway.replacements.values()))
    gateway.test_provider.uncertain = True
    await gateway.process_replacement(job.id)
    await gateway.process_replacement(job.id)
    assert job.state == "UNCERTAIN"
    assert gateway.test_provider.calls == 1


async def test_idle_reaper_does_not_kill_existing_connections(gateway):
    binding, node, generation = await gateway.acquire(route())

    class Writer:
        def close(self):
            raise AssertionError("active connection must survive")

    tunnel = await gateway.attach(binding.id, node.id, generation, Writer(), Writer())
    binding.last_active = binding.last_detached = 0
    await gateway.maintain()
    assert not binding.released and node.state == IPState.IN_USE
    await gateway.detach(tunnel)
    await gateway.maintain()
    assert not binding.released


async def test_graceful_retains_old_generation_until_detach(gateway):
    binding, old, generation = await gateway.acquire(route())

    class Writer:
        def __init__(self):
            self.closed = False

        def close(self):
            self.closed = True

    writer = Writer()
    tunnel = await gateway.attach(binding.id, old.id, generation, writer, writer)
    await gateway.rotate(binding.id, "graceful")
    assert old.state == IPState.DRAINING and not writer.closed
    with pytest.raises(GatewayError, match="排空"):
        await gateway.rotate(binding.id, "graceful")
    await gateway.detach(tunnel)
    assert old.state == IPState.REPLACING


async def test_hard_rotation_closes_old_connection(gateway):
    binding, old, generation = await gateway.acquire(route())

    class Writer:
        closed = False

        def close(self):
            self.closed = True

    writer = Writer()
    tunnel = await gateway.attach(binding.id, old.id, generation, writer, writer)
    await gateway.rotate(binding.id, "hard")
    assert writer.closed and old.state == IPState.REPLACING
    await gateway.detach(tunnel)


async def test_restart_preserves_used_node_and_logical_session(gateway):
    binding, old, _ = await gateway.acquire(route())
    async with gateway.lock:
        await gateway._persist()
    snapshot = await gateway.storage.load()
    assert snapshot["nodes"][old.id]["used"]
    assert snapshot["bindings"][binding.id]["session"] == "aaa"
    other_dir = gateway.config.data_dir.parent / "recovery"
    from dataclasses import replace

    config = replace(gateway.config, data_dir=other_dir)
    other_dir.mkdir()
    # Build a recovery DB through the same storage API, no live provider mutation.
    from rapip.storage import Storage

    store = Storage(other_dir)
    await store.open()
    await store.save(snapshot)
    await store.close()
    recovered = Gateway(config, provider_factory=gateway.provider_factory, probe=gateway.probe)
    await recovered.start(background=False, sync=False)
    try:
        assert recovered.nodes[old.id].state == IPState.IN_USE
        assert recovered.bindings[binding.id].session == "aaa"
        assert recovered.bindings[binding.id].node_id == old.id
        assert not recovered.replacements
    finally:
        await recovered.stop()


async def test_single_owner_lock(gateway):
    duplicate = Gateway(gateway.config)
    with pytest.raises(RuntimeError, match="one worker"):
        await duplicate.start(background=False)


async def test_storage_failure_stops_new_allocations(gateway, monkeypatch):
    async def fail(value, *args):
        raise OSError("disk unavailable")

    monkeypatch.setattr(gateway.storage, "save", fail)
    with pytest.raises(GatewayError, match="持久化"):
        await gateway.acquire(route())
    with pytest.raises(GatewayError):
        await gateway.acquire(route("02"))
    assert gateway.storage_failed


async def test_public_nodes_do_not_expose_credentials(gateway):
    payload = json.dumps([n.public() for n in gateway.nodes.values()])
    assert "upstream-secret" not in payload and "username" not in payload


async def test_correlated_probe_outage_does_not_burn_entire_pool(gateway):
    async def unavailable(node, timeout):
        return False

    gateway.probe = unavailable
    ids = list(gateway.nodes)
    await gateway._probe_candidates(ids)
    await gateway._probe_candidates(ids)
    assert gateway.stats()["fresh_count"] == 0
    assert not gateway.replacements


async def test_one_repeatedly_bad_node_is_replaced_when_peers_are_healthy(gateway):
    bad = next(iter(gateway.nodes.values()))

    async def probe(node, timeout):
        return node.id != bad.id

    gateway.probe = probe
    await gateway._probe_candidates(list(gateway.nodes))
    assert not gateway.replacements
    await gateway._probe_candidates(list(gateway.nodes))
    assert bad.state == IPState.REPLACING and len(gateway.replacements) == 1


async def test_pausing_account_preserves_current_binding(gateway):
    binding, node, _ = await gateway.acquire(route())
    gateway.accounts["test"].enabled = False
    same, same_node, _ = await gateway.acquire(route())
    assert same.id == binding.id and same_node.id == node.id
    with pytest.raises(GatewayError):
        await gateway.acquire(route("02"))


async def test_new_session_must_differ(gateway):
    binding, _, _ = await gateway.acquire(route())
    with pytest.raises(GatewayError, match="不同"):
        await gateway.rotate(binding.id, new_session="aaa")


async def test_pending_supplier_job_blocks_next_job_for_same_account(gateway):
    first, _, _ = await gateway.acquire(route("01"))
    second, _, _ = await gateway.acquire(route("02"))
    await gateway.rotate(first.id)
    await gateway.rotate(second.id)
    jobs = list(gateway.replacements.values())
    jobs[0].state = "UNCERTAIN"
    await gateway.process_replacement(jobs[1].id)
    assert gateway.test_provider.calls == 0


async def test_cooldown_blocks_new_supplier_copy(gateway):
    node = next(iter(gateway.nodes.values()))
    gateway.blacklist[node.ip] = time.time() + 3600
    await gateway._probe_candidates([node.id])
    assert node.state == IPState.QUARANTINED
    assert "冷却" in node.reason
