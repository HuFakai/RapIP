from rapip.replenishment import ReplenishmentRule, common_stock

HEADERS = {"Authorization": "Bearer test-api-token"}


def test_stock_intersection_accepts_supplier_asn_tuples_and_network_sizes():
    snapshots = [
        {
            "available_countries": {"US": 3, "DE": 2},
            "available_asns": {"1234": ["Example carrier", 4], "9999": ["Empty", 0]},
            "available_ip_ranges_16": {"203.0.0.0/16": 8},
            "available_ip_ranges_24": {"203.0.113.0/24": 5},
        },
        {
            "available_countries": {"US": 8, "GB": 7},
            "available_asns": {"1234": ["Example carrier", 2], "9999": ["Other", 9]},
            "available_ip_ranges_16": {"203.0.0.0/16": 3},
            "available_ip_ranges_24": {"192.0.2.0/24": 4},
        },
    ]
    assert common_stock(snapshots) == {
        "country": [{"value": "US", "count": 3, "name": ""}],
        "asn": [{"value": "1234", "count": 2, "name": "Example carrier"}],
        "ip_range": [{"value": "203.0.0.0/16", "count": 3, "name": ""}],
    }
    assert ReplenishmentRule(mode="original").selection("DE")["country_code"] == "DE"


async def test_only_supplier_stock_can_be_saved_as_a_target(client, gateway, monkeypatch):
    async def options():
        return {
            "available_countries": {"US": 4, "DE": 0},
            "available_asns": {"2914": ["Example", 2]},
            "available_ip_ranges_24": {"192.0.2.0/24": 1},
        }

    monkeypatch.setattr(gateway.test_provider, "replacement_options", options, raising=False)
    path = "/api/v1/groups/default/replenishment"
    stock = (await client.get(path + "-options", headers=HEADERS)).json()
    assert stock["account_count"] == 1
    assert {item["value"] for item in stock["country"]} == {"US"}
    rejected = await client.patch(path, json={"mode": "country", "country": "DE"}, headers=HEADERS)
    assert rejected.status_code == 409 and not gateway.replenishment
    rejected = await client.patch(
        path, json={"mode": "asn", "asns": [str(n) for n in range(1, 22)]}, headers=HEADERS
    )
    assert rejected.status_code == 422
    saved = await client.patch(path, json={"mode": "asn", "asns": ["2914"]}, headers=HEADERS)
    assert saved.status_code == 200
    assert ReplenishmentRule.model_validate(saved.json()).selection()["asn_numbers"] == ["2914"]
    assert not gateway.replacements and gateway.test_provider.calls == 0
