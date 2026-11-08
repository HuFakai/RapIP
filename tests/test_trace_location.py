import time

HEADERS = {"Authorization": "Bearer test-api-token"}


async def test_locate_latest_exact_ip_across_pages_and_keep_related_history(client, gateway):
    now = time.time()
    target = "203.0.113.71"
    old = {
        "id": "exact-old",
        "kind": "admission",
        "at": now - 90,
        "ip": target,
        "slot_id": "anchor-slot",
        "group_id": "default",
    }
    recent = {
        "id": "exact-recent",
        "kind": "webshare",
        "at": now - 80,
        "ip": target,
        "new_ip": "203.0.113.72",
        "slot_id": "anchor-slot",
        "group_id": "default",
    }
    later = [
        {
            "id": f"later{i:03}",
            "kind": "admission",
            "at": now - i,
            "ip": "203.0.113.72",
            "slot_id": "anchor-slot",
            "group_id": "default",
        }
        for i in range(60)
    ]
    await gateway.storage.save(gateway._snapshot(), [old, recent, *later])
    url = "/api/v1/lifecycle/chains?view=ips&event_view=true&page_size=25&q=" + target
    newest = (await client.get(url, headers=HEADERS)).json()
    assert newest["page"] == 1 and newest["anchor"] is None
    assert all(e["ip"] != target for e in newest["items"])
    located = (await client.get(url + "&locate=true", headers=HEADERS)).json()
    assert located["page"] == 3 and located["anchor"]["event_id"] == "exact-recent"
    assert located["location_checked"]
    assert any(e["id"] == "exact-recent" for e in located["items"])
    assert located["total"] == newest["total"] == 62
    ascending = (await client.get(url + "&order=asc&locate=true", headers=HEADERS)).json()
    assert ascending["page"] == 1 and ascending["anchor"]["event_id"] == "exact-recent"
    # Explicit filters still restrict what may be located.
    empty = (await client.get(url + "&group_id=missing&locate=true", headers=HEADERS)).json()
    assert empty["anchor"] is None and empty["total"] == 0
