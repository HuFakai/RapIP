"""Read-only single-exit diagnostics using the standalone pinned xykt/IPQuality runner."""

from __future__ import annotations

import asyncio
import contextlib
import copy
import ipaddress
import json
import os
import signal
import time
from collections import OrderedDict
from pathlib import Path

from .models import GatewayError, IPState, uid
from .quality_assessment import assess_report
from .socks import close_writer, read_address, target_text, upstream_connect

VENDOR = Path(__file__).parent / "vendor" / "ipquality"
REVISION = (VENDOR / "REVISION").read_text().strip()
ALLOWED_HOSTS = {"api.ipify.org", "ipinfo.check.place", "ipinfo.io"}
SOURCES = ["SCAMALYTICS", "ipapi", "AbuseIPDB", "IP2LOCATION", "IPQS"]


def normalize_report(raw, ip):
    if not isinstance(raw, dict) or not isinstance(raw.get("Head"), dict) or raw["Head"].get("IP") != ip:
        raise ValueError("report exit mismatch")

    def clean(value, depth=0):
        if depth > 8:
            return None
        if value is None or value in ("null", "", "N/A", "unknown"):
            return None
        if isinstance(value, dict):
            return {str(k)[:80]: clean(v, depth + 1) for k, v in list(value.items())[:100]}
        if isinstance(value, list):
            return [clean(v, depth + 1) for v in value[:100]]
        if isinstance(value, str):
            return value[:1000]
        return value if isinstance(value, (bool, int, float)) else None

    if any(not isinstance(raw.get(key, {}), dict) for key in ("Info", "Type", "Score", "Factor")):
        raise ValueError("invalid report sections")
    result = {key: clean(raw.get(key, {})) for key in ("Info", "Type", "Score", "Factor")}
    scores = result["Score"] or {}
    known = [name for name in SOURCES if scores.get(name) is not None]
    types = (result["Type"] or {}).get("Usage", {})
    evidence = bool(known or any(v is not None for v in types.values()))
    return {
        "ip": ip,
        "state": "PARTIAL" if evidence else "UNKNOWN",
        "scores_available": known,
        "missing_scores": [name for name in SOURCES if name not in known],
        **result,
        "engine": "xykt/IPQuality",
        "revision": REVISION,
        "scope": "数据库质量参考；不包含流媒体、邮件和 DNSBL 检测，不改变分组准入或触发 Replace",
    }


class IPQualityReports:
    def __init__(self, gateway):
        self.g = gateway
        self.running = None
        self.cache = OrderedDict()

    async def latest(self, node):
        report = self.cache.get(node.id)
        if (
            report
            and report["ip"] == node.ip
            and report["checked_at"] >= time.time() - self.g.observer.retention.probe_log_days * 86400
        ):
            return {**report, "assessment": assess_report(report)}
        obs = self.g.observer
        if not hasattr(obs, "db"):
            return None
        async with obs.lock:
            async with obs.db.execute(
                """SELECT payload FROM records WHERE kind='ipquality' AND ip=?
                AND json_extract(payload,'$.node_id')=? AND at>=? ORDER BY at DESC LIMIT 1""",
                (node.ip, node.id, time.time() - obs.retention.probe_log_days * 86400),
            ) as cursor:
                row = await cursor.fetchone()
        if not row:
            return None
        report = json.loads(row[0])
        return {**report, "assessment": assess_report(report)}

    async def run(self, node_id):
        if not self.g.report_methods["ipquality"]:
            raise GatewayError("METHOD_DISABLED", "IPQuality 检测已关闭，可在设置中启用", 403)
        if self.running:
            raise GatewayError("DIAGNOSTIC_BUSY", "已有 IPQuality 检测在执行，请稍后重试", 409)
        node = self.g.nodes.get(node_id)
        if not node or node.state not in {
            IPState.FRESH,
            IPState.RESERVED,
            IPState.QUARANTINED,
            IPState.IN_USE,
        }:
            raise GatewayError("NODE_BUSY", "该资源正在退役或替换，暂不能生成质量报告", 409)
        if ipaddress.ip_address(node.ip).version != 4:
            raise GatewayError("UNSUPPORTED_IP", "当前独立检测入口仅支持 IPv4", 400)
        self.running = node.id
        snapshot = copy.copy(node)
        token = (node.ip, node.membership_version)
        try:
            raw = await self.execute(snapshot)
            report = {
                **normalize_report(raw, snapshot.ip),
                "id": uid(),
                "node_id": node.id,
                "at": time.time(),
                "checked_at": time.time(),
                "account_id": node.account_id,
                "group_id": snapshot.group_id,
                "result": "reference",
                "replace_calls": 0,
            }
            if token != (node.ip, node.membership_version) or node.state not in {
                IPState.FRESH,
                IPState.RESERVED,
                IPState.QUARANTINED,
                IPState.IN_USE,
            }:
                raise GatewayError("STALE_DIAGNOSTIC", "检测期间资源发生变化，结果未保存，请重新检测", 409)
            self.cache[node.id] = report
            self.cache.move_to_end(node.id)
            while len(self.cache) > 200:
                self.cache.popitem(last=False)
            self.g.observer.enqueue("ipquality", report)
            await self.g.observer.flush(self.g)
            return report
        except (TimeoutError, OSError, ValueError, json.JSONDecodeError):
            raise GatewayError(
                "DIAGNOSTIC_FAILED",
                "IPQuality 检测超时、出口不匹配或报告无效；未改变资源状态，也未提交替换",
                502,
            ) from None
        finally:
            self.running = None

    async def execute(self, node):
        handlers = set()
        writers = set()
        transferred = 0

        async def bridge(reader, writer):
            nonlocal transferred
            task = asyncio.current_task()
            handlers.add(task)
            writers.add(writer)
            upstream = None
            try:
                if len(handlers) > 8:
                    return
                async with asyncio.timeout(15):
                    hello = await reader.readexactly(2)
                    methods = await reader.readexactly(hello[1])
                    if hello[0] != 5 or 0 not in methods:
                        return
                    writer.write(b"\x05\x00")
                    await writer.drain()
                    request = await reader.readexactly(4)
                    if request[:3] != b"\x05\x01\x00":
                        return
                    target = (
                        bytes([request[3]])
                        + await read_address(reader, request[3])
                        + await reader.readexactly(2)
                    )
                    destination = target_text(target)
                    host, port = destination.rsplit(":", 1)
                    if host not in ALLOWED_HOSTS or port != "443":
                        return
                    source, upstream, reply = await upstream_connect(
                        node, target, timeout=12, egress_proxy=self.g.config.egress_proxy
                    )
                    writers.add(upstream)
                    writer.write(reply)
                    await writer.drain()

                async def pump(source, destination):
                    nonlocal transferred
                    while chunk := await source.read(65536):
                        transferred += len(chunk)
                        if transferred > 16 * 1024 * 1024:
                            raise ConnectionError("diagnostic byte limit")
                        destination.write(chunk)
                        await destination.drain()

                tasks = [
                    asyncio.create_task(pump(reader, upstream)),
                    asyncio.create_task(pump(source, writer)),
                ]
                try:
                    done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
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
                writers.discard(upstream)
                writers.discard(writer)
                handlers.discard(task)

        process = None
        server = await asyncio.start_server(bridge, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        try:
            kwargs = {"user": 65534, "group": 65534, "extra_groups": []} if os.geteuid() == 0 else {}
            process = await asyncio.create_subprocess_exec(
                "bash",
                str(VENDOR / "runner.sh"),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                env={"PATH": "/usr/local/bin:/usr/bin:/bin", "LANG": "C.UTF-8", "TERM": "dumb"},
                cwd="/tmp",
                start_new_session=True,
                umask=0o077,
                **kwargs,
            )
            async with asyncio.timeout(180):
                process.stdin.write(f"{node.ip}\nsocks5h://127.0.0.1:{port}\n".encode())
                await process.stdin.drain()
                process.stdin.close()
                chunks = []
                size = 0
                while part := await process.stdout.read(16384):
                    size += len(part)
                    if size > 256 * 1024:
                        raise ValueError("oversize diagnostic")
                    chunks.append(part)
                code = await process.wait()
                if code:
                    raise ValueError(f"diagnostic process failed (exit {code})")
                return json.loads(b"".join(chunks))
        finally:
            if process:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGKILL)
                await process.wait()
            server.close()
            await server.wait_closed()
            for writer in list(writers):
                writer.close()
            for task in list(handlers):
                task.cancel()
            await asyncio.gather(*list(handlers), return_exceptions=True)
