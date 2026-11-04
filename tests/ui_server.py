"""Disposable local browser QA environment. No real credentials or provider network calls."""

import json
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn

from rapip.api import create_app
from rapip.config import Config
from rapip.gateway import Gateway
from rapip.models import Route


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
                        "replacement_budget": 2500,
                        "traffic_limit_gb": 250,
                    }
                ]
            )
        )
        provider = Provider()

        async def healthy(node, timeout):
            return True

        gateway = Gateway(config, provider_factory=lambda *args: provider, probe=healthy)
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
