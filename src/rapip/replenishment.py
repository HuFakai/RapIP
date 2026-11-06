"""Webshare replenishment preference, separate from admission and rotation."""

from __future__ import annotations

import asyncio
import ipaddress
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .models import GatewayError

NO_STOCK = "proxies_removed_doesnt_match_added"
RANDOM = {"type": "any", "count": 1}


class ReplenishmentRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["any", "country", "ip_range"] = "any"
    country: str = Field(default="", pattern=r"^([A-Z]{2})?$")
    ranges: list[str] = Field(default_factory=list, max_length=20)
    fallback: bool = True

    @model_validator(mode="after")
    def valid_selection(self):
        if self.mode == "country" and not self.country:
            raise ValueError("请输入两位国家代码")
        if self.mode == "ip_range":
            if not self.ranges:
                raise ValueError("请输入 IPv4 CIDR")
            if any(ipaddress.ip_network(value, strict=True).version != 4 for value in self.ranges):
                raise ValueError("仅支持 IPv4 CIDR")
            self.ranges = list(dict.fromkeys(self.ranges))
        return self

    def selection(self):
        if self.mode == "country":
            return {"type": "country", "country_code": self.country, "count": 1}
        if self.mode == "ip_range":
            return {"type": "ip_range", "ip_ranges": self.ranges, "count": 1}
        return dict(RANDOM)


async def preflight(provider, ip, selection):
    """Only an explicit supplier stock rejection authorizes random fallback."""
    async with asyncio.timeout(15):
        data = await provider.replace_selected(ip, selection, dry_run=True)
        for _ in range(10):
            if data.get("state") in {"validated", "completed", "failed"}:
                break
            if not data.get("id"):
                break
            await asyncio.sleep(0.5)
            data = await provider.replacement(str(data["id"]))
    if data.get("state") == "failed" and data.get("error_code") == NO_STOCK:
        return False
    if (
        data.get("state") not in {"validated", "completed"}
        or data.get("proxies_added") != 1
        or data.get("proxies_removed") != 1
    ):
        raise GatewayError("REPLENISHMENT_PREVIEW_FAILED", "Webshare 预检未确认可 1:1 替换")
    return True
