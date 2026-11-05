import pytest

from rapip.config import Account
from rapip.provider import ProviderError, Webshare


async def test_provider_rejects_cross_origin_pagination(gateway):
    provider = Webshare(Account("x"), "private-test-token", gateway.client)
    with pytest.raises(ProviderError):
        await provider.request("GET", "https://untrusted.example/steal")


async def test_paused_probes_do_not_admit_unverified_nodes(gateway):
    gateway.config.probe_enabled = False
    node = next(iter(gateway.nodes.values()))
    node.state = "QUARANTINED"

    async def should_not_run(node, timeout):
        raise AssertionError("network probe must be disabled")

    gateway.probe = should_not_run
    await gateway._probe_candidates([node.id])
    assert node.state == "QUARANTINED"


async def test_supplier_parameter_replacement_and_options_are_scoped_and_sanitized(gateway, monkeypatch):
    provider = Webshare(Account("x", plan_id="123"), "test-private-key", gateway.client)
    calls = []

    async def request(method, path, **kwargs):
        calls.append((method, path, kwargs))
        if method == "POST":
            return {"id": 7, "state": "validated"}
        return {
            "available_countries": {"US": 10},
            "available_asns": {},
            "available_ip_ranges_24": {},
            "password": "do-not-leak",
            "username": "private",
        }

    monkeypatch.setattr(provider, "request", request)
    options = await provider.replacement_options()
    assert "password" not in options and "username" not in options
    selection = {"type": "asn", "asn_numbers": ["2914"], "count": 1}
    await provider.replace_selected("198.51.100.1", selection, dry_run=True)
    assert calls[-1][2]["params"] == {"plan_id": "123"}
    assert calls[-1][2]["json"] == {
        "to_replace": {"type": "ip_address", "ip_addresses": ["198.51.100.1"]},
        "replace_with": [selection],
        "dry_run": True,
    }
