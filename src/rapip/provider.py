from __future__ import annotations

import asyncio
import random
from urllib.parse import urlparse

import aiohttp

from .config import Account


class ProviderError(Exception):
    def __init__(self, status: int, uncertain: bool = False, retry_after: float = 5):
        super().__init__(f"Webshare HTTP {status}" if status else "Webshare 网络请求失败")
        self.status, self.uncertain, self.retry_after = status, uncertain, retry_after


class Webshare:
    def __init__(self, account: Account, key: str, session: aiohttp.ClientSession):
        self.account, self._key, self.session = account, key, session

    async def request(self, method: str, path: str, **kwargs):
        url = path if path.startswith("https://") else f"https://proxy.webshare.io{path}"
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.netloc != "proxy.webshare.io":
            raise ProviderError(400)
        for attempt in range(3 if method == "GET" else 1):
            try:
                async with self.session.request(
                    method, url, headers={"Authorization": f"Token {self._key}"}, **kwargs
                ) as response:
                    if response.status >= 400:
                        try:
                            delay = min(120, max(1, float(response.headers.get("Retry-After", 5))))
                        except ValueError:
                            delay = 5
                        if method == "GET" and response.status in {429, 500, 502, 503, 504} and attempt < 2:
                            await asyncio.sleep(
                                delay if response.status == 429 else 2**attempt + random.random()
                            )
                            continue
                        raise ProviderError(
                            response.status, method == "POST" and response.status >= 500, delay
                        )
                    return await response.json()
            except (aiohttp.ClientError, TimeoutError):
                if method == "GET" and attempt < 2:
                    await asyncio.sleep(2**attempt)
                    continue
                raise ProviderError(0, method == "POST") from None

    def plan(self) -> dict:
        return {"plan_id": self.account.plan_id} if self.account.plan_id else {}

    async def list_nodes(self) -> list[dict]:
        params = {"mode": "direct", "page_size": 100, **self.plan()}
        url, rows = "/api/v2/proxy/list/", []
        seen = set()
        while url:
            if url in seen:
                raise ProviderError(502)
            seen.add(url)
            data = await self.request("GET", url, params=params)
            rows.extend(data["results"])
            url, params = data.get("next"), None
        return rows

    async def replace(self, ip: str, country: str) -> str:
        selection = (
            {"type": "any", "count": 1}
            if country == "any"
            else {"type": "country", "country_code": country, "count": 1}
        )
        data = await self.request(
            "POST",
            "/api/v3/proxy/replace/",
            params=self.plan(),
            json={
                "to_replace": {"type": "ip_address", "ip_addresses": [ip]},
                "replace_with": [selection],
                "dry_run": False,
            },
        )
        return str(data["id"])

    async def replacement(self, job_id: str) -> dict:
        return await self.request("GET", f"/api/v3/proxy/replace/{job_id}/", params=self.plan())
