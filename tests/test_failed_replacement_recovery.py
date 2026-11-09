from rapip.models import IPState, Route


async def test_failed_preflight_releases_fake_replacing_state_without_reusing_old_ip(gateway, monkeypatch):
    gateway.replenishment["default"] = {"mode": "country", "country": "US"}
    binding, old, _ = await gateway.acquire(Route.parse("admin-worker-01-session-first"))
    _, current, _ = await gateway.acquire(Route.parse("admin-worker-01-session-second"))
    job = next(j for j in gateway.replacements.values() if j.node_id == old.id)

    async def timeout(*args, **kwargs):
        raise TimeoutError()

    monkeypatch.setattr(gateway.test_provider, "replace_selected", timeout, raising=False)
    for _ in range(3):
        await gateway.process_replacement(job.id)
    assert job.state == "FAILED" and job.attempts == 0
    assert old.state == IPState.QUARANTINED and old.replacement_error
    assert "超时" in old.replacement_error
    assert old.binding_id is None
    assert gateway.bindings[binding.id].node_id == current.id
    assert gateway.bindings[binding.id].session == "second"
    assert not gateway._fresh_eligible(old)
    await gateway._probe_candidates([old.id])
    await gateway.process_replacement(job.id)
    assert old.state == IPState.QUARANTINED and gateway.test_provider.calls == 0
    saved = await gateway.storage.load()
    assert saved["nodes"][old.id]["replacement_error"]


async def test_repair_historical_failure_is_once_and_does_not_touch_uncertain_jobs(gateway):
    node, unknown = list(gateway.nodes.values())[:2]
    async with gateway.changed:
        gateway._retire(node, "test")
        gateway._retire(unknown, "test")
        failed = next(j for j in gateway.replacements.values() if j.node_id == node.id)
        uncertain = next(j for j in gateway.replacements.values() if j.node_id == unknown.id)
        failed.state, failed.error = "FAILED", "预检未确认"
        uncertain.state = "UNCERTAIN"
        await gateway._persist()
    assert node.state == IPState.QUARANTINED and node.replacement_error
    assert not gateway.quality.eligible(node)
    assert unknown.state == IPState.REPLACING and not unknown.replacement_error
    count = len(gateway.lifecycle.pending)
    async with gateway.changed:
        await gateway._persist()
    assert len(gateway.lifecycle.pending) == count
    assert gateway.test_provider.calls == 0
