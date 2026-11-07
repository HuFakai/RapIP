import pytest

AUTH = {"Authorization": "Bearer test-api-token"}


async def test_ip_path_uses_supplier_replacements_not_link_switch(client, gateway):
    from rapip.models import Route

    bind, old, _ = await gateway.acquire(Route("admin", "before", "01"))
    _, pooled, _ = await gateway.acquire(Route("admin", "after", "01"))
    q = {"view": "ips", "q": old.ip}
    initial = (await client.get("/api/v1/lifecycle/chains", params=q, headers=AUTH)).json()
    assert [path["slot_id"] for path in initial["paths"]] == [old.slot_id]
    assert initial["paths"][0]["changes"] == []
    assert any(
        e["kind"] == "switched" and e["old_ip"] == old.ip and e["ip"] == pooled.ip
        for chain in initial["items"]
        for e in chain["events"]
    )

    first = next(iter(gateway.replacements.values()))
    await gateway.process_replacement(first.id)
    middle = next(n for n in gateway.nodes.values() if n.ip == first.new_ip)
    async with gateway.changed:
        gateway._retire(middle, "测试下一次供应商更换")
        await gateway._persist()
    second = next(j for j in gateway.replacements.values() if j.node_id == middle.id)
    await gateway.process_replacement(second.id)
    for ip in (old.ip, middle.ip, second.new_ip):
        response = await client.get("/api/v1/lifecycle/chains", params={**q, "q": ip}, headers=AUTH)
        assert response.status_code == 200
        (path,) = response.json()["paths"]
        assert path["slot_id"] == old.slot_id and path["current"]["ip"] == second.new_ip
        assert [(e["ip"], e["new_ip"]) for e in path["changes"]] == [
            (middle.ip, second.new_ip),
            (old.ip, middle.ip),
        ]
        assert any(e.get("new_ip") == second.new_ip for c in response.json()["items"] for e in c["events"])
    assert bind.node_id == pooled.id


@pytest.mark.parametrize("unused", [True, False])
async def test_release_trace_distinguishes_unused_reservation(client, gateway, unused):
    node = next(iter(gateway.nodes.values()))
    route, _ = await gateway.workers.issue(node_id=node.id, key="reserve-example")
    bind = gateway.binding_for(route.slot)
    query = {"view": "links", "binding_id": bind.id}
    response = await client.get("/api/v1/lifecycle/chains", params=query, headers=AUTH)
    assert response.json()["items"][0]["status"] == "RESERVED"
    if not unused:
        await gateway.acquire(route)
    await gateway.release(bind.id, "hard")
    response = await client.get("/api/v1/lifecycle/chains", params=query, headers=AUTH)
    release = next(e for c in response.json()["items"] for e in c["events"] if e["kind"] == "released")
    assert release["release_action"] == ("unused_reservation" if unused else "used_exit")
    assert not gateway.replacements


async def test_worker_path_follows_new_binding_after_release(client, gateway):
    from rapip.models import Route

    old, _, _ = await gateway.acquire(Route("admin", "first", "01"))
    await gateway.release(old.id, "hard")
    latest, _, _ = await gateway.acquire(Route("admin", "next", "01"))
    await gateway.acquire(Route("admin", "01", "02"))  # Matches search text, not the selected worker.
    response = await client.get(
        "/api/v1/lifecycle/chains", params={"group_id": "default", "worker": "01"}, headers=AUTH
    )
    assert response.status_code == 200
    data = response.json()
    assert data["focus"]["session"] == latest.session
    assert {e["binding_id"] for c in data["items"] for e in c["events"]} == {old.id, latest.id}
