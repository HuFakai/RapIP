"""Bounded, opt-in Ping0 self-query. A challenge is a site-access observation, never a quality verdict."""

from __future__ import annotations

import asyncio
import copy
import ipaddress
import json
import re
import time
from contextlib import asynccontextmanager

from bs4 import BeautifulSoup
from curl_cffi.requests import AsyncSession

from .models import GatewayError, IPState, uid
from .socks import address_bytes, close_writer, read_address, target_text, upstream_connect

MAX_BODY = 2 * 1024 * 1024
FIELDS = {
    "IP 位置": "location",
    "IP位置": "location",
    "ASN": "asn",
    "ASN 所有者": "asn_owner",
    "ASN所有者": "asn_owner",
    "企业": "company",
    "IP 类型": "ip_type",
    "IP类型": "ip_type",
    "风控值": "risk",
    "原生 IP": "native",
    "原生IP": "native",
    "共享人数": "sharing",
}


def parse_page(body, status, expected_ip, headers=None):
    headers = {k.lower(): str(v).lower() for k, v in (headers or {}).items()}
    text = body[:MAX_BODY].decode("utf-8", errors="replace")
    soup = BeautifulSoup(text, "html.parser")
    plain = soup.get_text(" ", strip=True)
    report = {
        "ip": expected_ip,
        "state": "UNAVAILABLE",
        "site_access": "UNKNOWN",
        "http_status": status,
        "fields": {},
        "note": "",
    }
    # A real report can load Turnstile scripts elsewhere, so detect challenge documents specifically.
    challenge = (
        headers.get("cf-mitigated") == "challenge"
        or soup.find(id="challenge-running") is not None
        or soup.find(id="challenge-form") is not None
        or ("just a moment" in (soup.title.get_text().lower() if soup.title else ""))
        or ("challenges.cloudflare.com" in text and not soup.select("tr"))
        or ("人机验证" in plain and "风控值" not in plain)
    )
    if status == 429:
        report.update(site_access="RATE_LIMITED", note="Ping0 限流，停止本次查询；稍后再试，不换出口重试")
        return report
    if challenge:
        report.update(
            site_access="CHALLENGE", note="本次访问触发站点验证；没有取得报告，不代表 IP 必然质量差"
        )
        return report
    if status != 200:
        report.update(site_access="HTTP_ERROR", note=f"Ping0 返回 HTTP {status}，未取得质量报告")
        return report
    found_ip = None
    fields = {}
    for row in soup.select("tr"):
        cells = row.find_all(["td", "th"], recursive=False)
        if len(cells) < 2:
            continue
        label = cells[0].get_text(" ", strip=True)
        label = re.sub(r"[（(].*?[）)]", "", label).strip()
        value = cells[1].get_text(" ", strip=True)
        if label in {"IP 地址", "IP地址"}:
            match = re.search(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])", value)
            found_ip = match.group(0) if match else found_ip
        if key := FIELDS.get(label):
            fields[key] = re.sub(r"\s*说明\s*", "", value)[:500]
    # Never accept a page for a different IP or a generic homepage as this node's report.
    if found_ip != expected_ip:
        report.update(site_access="UNVERIFIED", note="页面未明确确认本次检测的出口 IP，未采纳报告")
        return report
    if not fields:
        report.update(site_access="PARSE_CHANGED", note="页面未返回可识别的质量字段，保留原网页入口")
        return report
    report.update(
        state="PARTIAL" if len(fields) < 8 else "READY",
        site_access="DIRECT",
        fields=fields,
        note="这是 Ping0 的站点参考报告；本次无验证直接访问，不等于所有网站均可用，也不证明真实家宽",
    )
    match = re.search(r"(?<!\d)(\d{1,3}(?:\.\d+)?)\s*%", fields.get("risk", ""))
    if match and 0 <= float(match.group(1)) <= 100:
        report["risk_percent"] = float(match.group(1))
    return report


@asynccontextmanager
async def exit_proxy(gateway, node):
    """Temporary local SOCKS proxy to one fixed exit and one fixed HTTPS hostname."""
    handlers, writers = set(), set()
    transferred = 0

    async def handle(reader, writer):
        nonlocal transferred
        task = asyncio.current_task()
        handlers.add(task)
        writers.add(writer)
        upstream = None
        try:
            async with asyncio.timeout(15):
                header = await reader.readexactly(2)
                methods = await reader.readexactly(header[1])
                if header[0] != 5 or 0 not in methods or len(handlers) > 2:
                    return
                writer.write(b"\x05\x00")
                await writer.drain()
                req = await reader.readexactly(4)
                if req[:3] != b"\x05\x01\x00":
                    return
                target = bytes([req[3]]) + await read_address(reader, req[3]) + await reader.readexactly(2)
                if target_text(target) != "ping0.cc:443":
                    return
                source, upstream, reply = await upstream_connect(
                    node, address_bytes("ping0.cc", 443), 12, gateway.config.egress_proxy
                )
                writers.add(upstream)
                writer.write(reply)
                await writer.drain()

            async def pump(src, dest):
                nonlocal transferred
                while chunk := await src.read(65536):
                    transferred += len(chunk)
                    if transferred > 4 * MAX_BODY:
                        raise ConnectionError("diagnostic size limit")
                    dest.write(chunk)
                    await dest.drain()

            tasks = [asyncio.create_task(pump(reader, upstream)), asyncio.create_task(pump(source, writer))]
            try:
                done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                for item in done:
                    item.result()
            finally:
                for item in tasks:
                    item.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
        except (Exception, asyncio.CancelledError):
            pass
        finally:
            await close_writer(upstream)
            await close_writer(writer)
            writers.discard(writer)
            writers.discard(upstream)
            handlers.discard(task)

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    try:
        yield f"socks5h://127.0.0.1:{server.sockets[0].getsockname()[1]}"
    finally:
        server.close()
        await server.wait_closed()
        for writer in list(writers):
            writer.close()
        tasks = list(handlers)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


class Ping0Reports:
    def __init__(self, gateway):
        self.g = gateway
        self.running = None
        self.next_at = 0
        self.cache = {}

    async def latest(self, node):
        cutoff = time.time() - self.g.observer.retention.probe_log_days * 86400
        cached = self.cache.get(node.id)
        if cached and cached["ip"] == node.ip and cached["checked_at"] >= cutoff:
            return cached
        obs = self.g.observer
        if not hasattr(obs, "db"):
            return None
        async with obs.lock:
            async with obs.db.execute(
                "SELECT payload FROM records WHERE kind='ping0' AND ip=? AND at>=? ORDER BY at DESC LIMIT 1",
                (node.ip, cutoff),
            ) as cur:
                row = await cur.fetchone()
        return json.loads(row[0]) if row else None

    async def execute(self, node):
        data = bytearray()

        def collect(chunk):
            if len(data) + len(chunk) > MAX_BODY:
                raise ValueError("Ping0 response too large")
            data.extend(chunk)

        async with exit_proxy(self.g, node) as proxy:
            async with AsyncSession(max_clients=1, trust_env=False) as session:
                response = await session.get(
                    "https://ping0.cc/",
                    proxy=proxy,
                    impersonate="chrome",
                    timeout=25,
                    allow_redirects=False,
                    content_callback=collect,
                )
        return parse_page(bytes(data), response.status_code, node.ip, dict(response.headers))

    async def run(self, node_id):
        if not self.g.report_methods.get("ping0", False):
            raise GatewayError("METHOD_DISABLED", "Ping0 查询未开启，请在质量检测源设置启用", 403)
        if self.running:
            raise GatewayError("DIAGNOSTIC_BUSY", "已有 Ping0 查询正在执行", 409)
        if time.time() < self.next_at:
            raise GatewayError(
                "DIAGNOSTIC_COOLDOWN",
                f"Ping0 查询冷却中，请在 {int(self.next_at - time.time()) + 1} 秒后重试",
                429,
            )
        node = self.g.nodes.get(node_id)
        if not node or node.state not in {
            IPState.FRESH,
            IPState.IN_USE,
            IPState.RESERVED,
            IPState.QUARANTINED,
        }:
            raise GatewayError("NODE_BUSY", "该出口正在更换或退役，暂不能查询")
        ipaddress.ip_address(node.ip)
        previous = await self.latest(node)
        if previous and time.time() - previous["checked_at"] < 600:
            return {**previous, "cached": True}
        self.running = node.id
        self.next_at = time.time() + 30
        snapshot = copy.copy(node)
        token = node.ip, node.membership_version, node.state, node.binding_id
        try:
            try:
                async with asyncio.timeout(35):
                    result = await self.execute(snapshot)
            except Exception:
                result = {
                    "ip": snapshot.ip,
                    "state": "UNAVAILABLE",
                    "site_access": "NETWORK_ERROR",
                    "fields": {},
                    "note": "Ping0 查询网络失败；未更换 IP、未改变绑定或分组准入",
                }
            if (node.ip, node.membership_version, node.state, node.binding_id) != token:
                raise GatewayError("STALE_DIAGNOSTIC", "检测期间资源或绑定发生变化，本次结果未保存")
            if result["site_access"] in {"CHALLENGE", "RATE_LIMITED"}:
                self.next_at = time.time() + 300
            report = {
                **result,
                "id": uid(),
                "node_id": node.id,
                "at": time.time(),
                "checked_at": time.time(),
                "account_id": node.account_id,
                "group_id": node.group_id,
                "result": "reference",
                "replace_calls": 0,
                "engine": "Ping0",
            }
            self.cache[node.id] = report
            while len(self.cache) > 200:
                self.cache.pop(next(iter(self.cache)))
            self.g.observer.enqueue("ping0", report)
            await self.g.observer.flush(self.g)
            return report
        finally:
            self.running = None
