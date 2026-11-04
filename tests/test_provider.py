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
