"""Disposable local browser QA environment. No real credentials or provider network calls."""

import json
import tempfile
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

import uvicorn

from rapip.api import create_app
from rapip.config import Config
from rapip.gateway import Gateway
from rapip.models import Route
from rapip.quotas import normalize_usage


class Provider:
    def __init__(self):
        self.rows = [
            {
                "id": str(i),
                "proxy_address": f"198.51.100.{i + 10}",
                "port": 8000,
                "username": "qa",
                "password": "qa",
                "country_code": "US",
                "valid": True,
            }
            for i in range(6)
        ]
        self.counter = 0

    async def list_nodes(self):
        return [dict(row) for row in self.rows]

    async def usage(self):
        now = time.time()
        return normalize_usage(
            {
                "id": 1 if self.rows else 2,
                "plan": 1 if self.rows else 2,
                "start_date": datetime.fromtimestamp(now - 86400, timezone.utc).isoformat(),
                "end_date": datetime.fromtimestamp(now + 29 * 86400, timezone.utc).isoformat(),
            },
            {
                "id": 1 if self.rows else 2,
                "bandwidth_limit": 250,
                "proxy_replacements_total": 1000 if self.rows else 500,
                "proxy_replacements_used": 1 if self.rows else 0,
                "proxy_replacements_available": 999 if self.rows else 500,
            },
            {"bandwidth_total": 5_000_000},
        )

    async def replace(self, ip, country):
        self.counter += 1
        row = next(row for row in self.rows if row["proxy_address"] == ip)
        row["proxy_address"] = f"198.51.100.{30 + self.counter}"
        row["id"] = str(self.counter + 100)
        return str(self.counter)

    async def replacement(self, job_id):
        return {"state": "completed", "proxies_added": 1, "proxies_removed": 1}


def main():
    with tempfile.TemporaryDirectory(prefix="rapip-browser-qa-") as directory:
        root = Path(directory)
        config = Config(
            data_dir=root / "data",
            config_dir=root / "config",
            admin_password="qa-password-only",
            socks_password="qa-proxy-password",
            admin_token="qa-script-token",
            http_port=18081,
        )
        config.config_dir.mkdir()
        (config.config_dir / "accounts.json").write_text(
            json.dumps(
                [
                    {
                        "account_id": "qa",
                        "name": "浏览器验证 · 模拟账号",
                        "notes": "项目 A · 采购渠道说明",
                        "replacement_budget": 2500,
                        "traffic_limit_gb": 250,
                    },
                    {"account_id": "qaempty", "name": "可移除的模拟账号", "enabled": False},
                ]
            )
        )
        provider = Provider()

        async def healthy(node, timeout):
            return True

        empty_provider = Provider()
        empty_provider.rows = []
        gateway = Gateway(
            config,
            provider_factory=lambda account, *args: (
                empty_provider if account.account_id == "qaempty" else provider
            ),
            probe=healthy,
        )

        async def network(node, policy):
            return {
                "complete": True,
                "successes": policy.samples,
                "samples": policy.samples,
                "max_latency_ms": 420,
                "exits": [node.ip],
                "exit_match": True,
            }

        gateway.quality.network_check = network

        async def diagnostic(node):
            return {
                "Head": {"IP": node.ip},
                "Info": {"ASN": "62874", "Organization": "模拟机房，仅用于界面验证"},
                "Type": {
                    "Usage": {source: "机房" for source in ["IPinfo", "ipapi", "AbuseIPDB", "IP2LOCATION"]}
                },
                "Score": {
                    "SCAMALYTICS": "0",
                    "AbuseIPDB": "0",
                    "IP2LOCATION": "91",
                    "ipapi": "0.23%",
                    "IPQS": "null",
                },
                "Factor": {
                    "VPN": {"IPinfo": False, "IP2LOCATION": True},
                    "Proxy": {"IPinfo": False, "IP2LOCATION": True},
                    "Server": {"IPinfo": True, "ipapi": True},
                },
            }

        gateway.ipquality.execute = diagnostic
        app = create_app(config, gateway, lifecycle=False)

        @asynccontextmanager
        async def lifecycle(app):
            await gateway.start()
            await gateway.acquire(Route("admin", "qa-session".replace("-", ""), "01"))
            try:
                yield
            finally:
                await gateway.stop()

        app.router.lifespan_context = lifecycle
        uvicorn.run(app, host="127.0.0.1", port=18081, log_level="warning")


if __name__ == "__main__":
    main()
