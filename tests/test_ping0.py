import asyncio
import copy
import time

import pytest

from rapip.models import GatewayError
from rapip.ping0 import parse_report


def page(ip="1.1.1.1", risk=72):
    return f"""<html><head><title>{ip}-IP 查询</title></head><body>
    <div class="line loc"><div class="name">位置</div><div class="content"><div>美国 得克萨斯州 达拉斯<span class="report">错误提交</span></div><div>探测时间 : 2026-09-22</div></div></div>
    <div class="line asn"><div class="content"><a>AS174</a></div></div>
    <div class="line asnname"><div class="content">Cogent Communications, LLC <span>{{{{asndomain}}}}</span></div></div>
    <div class="line orgname"><div class="content">示例运营商</div></div>
    <div class="line line-iptype"><div class="name">IP 类型</div><div class="content"><span>家庭宽带 IP</span><span>代理 IP</span><a class="ispwhy">为什么</a></div></div>
    <div class="line line-risk"><div class="content"><div class="riskitem" title="0-15"><span>0</span></div><div class="riskitem riskcurrent"><span class="value">{risk}%</span><span class="lab">极度风险</span></div></div></div>
    <div class="line line-nativeip"><div class="content"><span>原生 IP</span></div></div>
    <div class="line line-usecount"><div class="content"><div>1 - 10 (极好)</div></div></div>
    <script>window.evil = 'never run';</script></body></html>"""


def test_ping0_extracts_fields_without_source_help_or_scripts():
    report = parse_report(page(), "1.1.1.1")
    fields = report["fields"]
    assert fields["location"] == "美国 得克萨斯州 达拉斯"
    assert fields["risk"] == 72 and fields["shared_users"] == "1 - 10 (极好)"
    assert fields["asn"] == "AS174" and fields["asn_owner"] == "Cogent Communications, LLC"
    assert fields["ip_type"] == "家庭宽带 IP 代理 IP"
    assert fields["native_ip"] == "原生 IP" and report["missing_fields"] == []
    assert parse_report(page(risk=0), "1.1.1.1")["fields"]["risk"] == 0
    assert parse_report(page(risk=999), "1.1.1.1")["fields"]["risk"] is None


@pytest.mark.parametrize(
    "html",
    [
        "<title>Just a moment...</title>",
        "<html>login</html>",
        page("8.8.8.8"),
        "<title>1.1.1.1-IP</title><div>风控值</div>",
    ],
)
def test_ping0_rejects_blocked_wrong_ip_and_changed_markup(html):
    with pytest.raises(ValueError):
        parse_report(html, "1.1.1.1")


async def test_ping0_readonly_retention_and_independent_switches(gateway, monkeypatch):
    node = next(iter(gateway.nodes.values()))
    node.ip = "1.1.1.1"
    before = copy.deepcopy(node.public())

    async def fetch(ip):
        return page(ip)

    monkeypatch.setattr(gateway.ping0, "fetch", fetch)
    result = await gateway.ping0.run(node.id)
    assert result["fields"]["risk"] == 72 and result["replace_calls"] == 0
    assert node.public() == before and not gateway.bindings and gateway.test_provider.calls == 0
    gateway.ping0.cache.clear()
    assert (await gateway.ping0.latest(node))["fields"]["risk"] == 72
    gateway.report_methods["ping0"] = False
    assert gateway.report_methods["ipquality"]
    with pytest.raises(GatewayError, match="已关闭"):
        await gateway.ping0.run(node.id)
    assert await gateway.ping0.latest(node)
    async with gateway.observer.lock:
        await gateway.observer.db.execute(
            "UPDATE records SET at=? WHERE kind='ping0'", (time.time() - 400 * 86400,)
        )
        await gateway.observer.db.commit()
    assert await gateway.ping0.latest(node) is None


async def test_ping0_stale_concurrency_and_throttling(gateway, monkeypatch):
    node = next(iter(gateway.nodes.values()))
    node.ip = "1.1.1.1"
    started, finish = asyncio.Event(), asyncio.Event()

    async def fetch(ip):
        started.set()
        await finish.wait()
        return page(ip)

    monkeypatch.setattr(gateway.ping0, "fetch", fetch)
    task = asyncio.create_task(gateway.ping0.run(node.id))
    await started.wait()
    with pytest.raises(GatewayError, match="间隔"):
        await gateway.ping0.run(node.id)
    node.membership_version += 1
    finish.set()
    with pytest.raises(GatewayError, match="出口变化"):
        await task
    assert gateway.ping0.running is None and not gateway.ping0.cache
    with pytest.raises(GatewayError, match="间隔"):
        await gateway.ping0.run(node.id)


async def test_report_switch_api_auth_and_does_not_remove_reports(client, gateway):
    path = "/api/v1/quality/report-methods"
    assert (await client.get(path)).status_code == 401
    headers = {"Authorization": "Bearer test-api-token"}
    assert (await client.patch(path, json={"ping0": False}, headers=headers)).json() == {
        "ping0": False,
        "ipquality": True,
    }
    assert (await client.patch(path, json={"ipquality": False}, headers=headers)).json() == {
        "ping0": False,
        "ipquality": False,
    }
    node = next(iter(gateway.nodes.values()))
    assert not (await client.get(f"/api/v1/nodes/{node.id}/ping0", headers=headers)).json()["enabled"]
    with pytest.raises(GatewayError, match="已关闭"):
        await gateway.ipquality.run(node.id)
    assert gateway._snapshot()["report_methods"] == {"ping0": False, "ipquality": False}


def test_ping0_identifies_captcha_and_preserves_fractional_risk():
    assert parse_report(page(risk=12.5), "1.1.1.1")["fields"]["risk"] == 12.5
    with pytest.raises(GatewayError, match="人机验证页"):
        parse_report(
            '<html><script src="https://challenges.cloudflare.com/turnstile/v0/api.js"></script></html>',
            "1.1.1.1",
        )
