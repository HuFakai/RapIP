"""Read-only public-IP lookups. Parse text only; never execute remote HTML or scripts."""

from __future__ import annotations

import ipaddress
import json
import re
import time
from collections import OrderedDict
from html.parser import HTMLParser

import aiohttp

from .models import GatewayError, IPState, uid


class Element:
    def __init__(self, tag="", attrs=()):
        self.tag, self.attrs, self.children = tag, dict(attrs), []

    def has(self, name):
        return name in self.attrs.get("class", "").split()

    def find(self, name):
        if self.has(name):
            return self
        return next(
            (found for child in self.children if isinstance(child, Element) and (found := child.find(name))),
            None,
        )

    def text(self):
        if self.tag in {"script", "style", "svg"} or any(
            self.has(c) for c in ("fielddesc", "report", "ispwhy")
        ):
            return ""
        text = " ".join(child if isinstance(child, str) else child.text() for child in self.children)
        return re.sub(r"\s+", " ", re.sub(r"\{\{.*?\}\}", "", text)).strip()


class Document(HTMLParser):
    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.root = Element()
        self.stack = [self.root]
        self.lines = {}
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        if len(self.stack) > 128:
            raise ValueError("excessive HTML depth")
        node = Element(tag, attrs)
        self.stack[-1].children.append(node)
        if node.has("line"):
            for name in node.attrs.get("class", "").split():
                if name != "line":
                    self.lines[name] = node
        if tag not in {
            "area",
            "base",
            "br",
            "col",
            "embed",
            "hr",
            "img",
            "input",
            "link",
            "meta",
            "param",
            "source",
            "track",
            "wbr",
        }:
            self.stack.append(node)

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                break

    def handle_data(self, data):
        self.stack[-1].children.append(data)


def parse_report(html, ip):
    document = Document(html)
    # Prevent challenge, login, wrong-IP and unrelated pages from becoming a quality report.
    title = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
    if not title or not title[1].strip().startswith(ip + "-"):
        if "turnstile" in html.lower() or "AliyunCaptchaConfig" in html:
            raise GatewayError(
                "PING0_VERIFICATION_REQUIRED",
                "Ping0 对服务器返回了人机验证页，未取得质量数据；请打开 Ping0 原网页查看。不会自动执行人机验证或更换 IP",
                502,
            )
        raise ValueError("IP mismatch or blocked page")

    def content(name):
        line = document.lines.get(name)
        return line.find("content") if line else None

    def text(name):
        node = content(name)
        return node.text().rstrip(" —")[:600] if node else None

    risk = content("line-risk")
    current = risk.find("riskcurrent") if risk else None
    value = current.find("value") if current else None
    score = re.fullmatch(r"(\d{1,3}(?:\.\d+)?)%", value.text()) if value else None
    location = text("loc")
    fields = {
        "location": location.split("探测时间")[0].strip() if location else None,
        "asn": text("asn"),
        "asn_owner": text("asnname"),
        "company": text("orgname"),
        "ip_type": text("line-iptype"),
        "risk": float(score[1]) if score and float(score[1]) <= 100 else None,
        "risk_label": current.find("lab").text() if current and current.find("lab") else None,
        "native_ip": text("line-nativeip"),
        "shared_users": text("line-usecount"),
    }
    if not fields["asn"] or not any(fields[k] is not None for k in ("ip_type", "risk", "native_ip")):
        raise ValueError("No quality fields")
    return {
        "ip": ip,
        "engine": "Ping0",
        "source_url": f"https://ping0.cc/ip/{ip}",
        "state": "REFERENCE",
        "fields": fields,
        "missing_fields": [key for key, value in fields.items() if value is None],
        "llm_note": "大模型检测需在 Ping0 网页手动触发，本报告不自动调用。",
        "scope": "Ping0 数据库参考；风控值越高风险越大。家宽和原生标签不保证真实家庭线路或业务可用；共享人数是站点过去一年估算值。未改变分组准入、绑定或更换额度。",
    }


class Ping0Reports:
    def __init__(self, gateway):
        self.g, self.running = gateway, None
        self.cache = OrderedDict()
        self.last_request = 0

    async def latest(self, node):
        cutoff = time.time() - self.g.observer.retention.probe_log_days * 86400
        report = self.cache.get(node.id)
        if report and report["ip"] == node.ip and report["checked_at"] >= cutoff:
            return report
        obs = self.g.observer
        async with obs.lock:
            async with obs.db.execute(
                "SELECT payload FROM records WHERE kind='ping0' AND ip=? AND json_extract(payload,'$.node_id')=? AND at>=? ORDER BY at DESC LIMIT 1",
                (node.ip, node.id, cutoff),
            ) as cursor:
                row = await cursor.fetchone()
        return json.loads(row[0]) if row else None

    async def fetch(self, ip):
        # Fixed origin + parsed public address; no redirects or arbitrary URL fetching.
        async with self.g.client.get(
            f"https://ping0.cc/ip/{ip}",
            allow_redirects=False,
            timeout=aiohttp.ClientTimeout(total=25),
            headers={"User-Agent": "RapIP/0.2.6 (+IP quality lookup)"},
        ) as response:
            if response.status != 200:
                raise ValueError("lookup refused")
            body = bytearray()
            async for chunk in response.content.iter_chunked(65536):
                body.extend(chunk)
                if len(body) > 2_000_000:
                    raise ValueError("page too large")
            return body.decode("utf-8", errors="replace")

    async def run(self, node_id):
        if not self.g.report_methods["ping0"]:
            raise GatewayError("METHOD_DISABLED", "Ping0 检测已关闭，可在设置中启用", 403)
        node = self.g.nodes.get(node_id)
        if not node or node.state not in {
            IPState.FRESH,
            IPState.RESERVED,
            IPState.IN_USE,
            IPState.QUARANTINED,
        }:
            raise GatewayError("NODE_BUSY", "出口正在退役或替换，不能查询")
        if not ipaddress.ip_address(node.ip).is_global:
            raise GatewayError("INVALID_IP", "Ping0 只查询公共 IP", 422)
        if self.running or time.monotonic() - self.last_request < 10:
            raise GatewayError("DIAGNOSTIC_BUSY", "Ping0 每次查询至少间隔 10 秒，请稍后重试", 429)
        self.running, self.last_request = node.id, time.monotonic()
        token = node.ip, node.membership_version
        try:
            report = parse_report(await self.fetch(node.ip), node.ip)
            if token != (node.ip, node.membership_version) or node.state in {
                IPState.ARCHIVED,
                IPState.REPLACING,
                IPState.DRAINING,
            }:
                raise GatewayError("STALE_DIAGNOSTIC", "检测期间出口变化，请重新检测")
            report.update(
                id=uid(),
                node_id=node.id,
                account_id=node.account_id,
                group_id=node.group_id,
                checked_at=time.time(),
                at=time.time(),
                result="reference",
                replace_calls=0,
            )
            self.cache[node.id] = report
            while len(self.cache) > 200:
                self.cache.popitem(last=False)
            self.g.observer.enqueue("ping0", report)
            await self.g.observer.flush(self.g)
            return report
        except (aiohttp.ClientError, TimeoutError, ValueError):
            raise GatewayError(
                "DIAGNOSTIC_FAILED",
                "Ping0 超时、限流或页面结构变化，未取得有效报告；可打开来源网页核对。未更换 IP",
                502,
            ) from None
        finally:
            self.running = None
