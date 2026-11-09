"""Webshare replenishment preference, separate from admission and rotation."""

from __future__ import annotations

import asyncio
import ipaddress
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .models import GatewayError

NO_STOCK = "proxies_removed_doesnt_match_added"
RANDOM = {"type": "any", "count": 1}


class ReplenishmentRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["any", "original", "country", "asn", "ip_range"] = "any"
    country: str = Field(default="", pattern=r"^([A-Z]{2})?$")
    asns: list[str] = Field(default_factory=list, max_length=20)
    ranges: list[str] = Field(default_factory=list, max_length=20)
    fallback: bool = True

    @model_validator(mode="after")
    def valid_selection(self):
        if self.mode == "country" and not self.country:
            raise ValueError("请输入两位国家代码")
        if self.mode == "asn":
            if not self.asns or any(
                not re.fullmatch(r"[1-9][0-9]{0,9}", v) or int(v) >= 2**32 for v in self.asns
            ):
                raise ValueError("请选择有效 ASN")
            self.asns = list(dict.fromkeys(self.asns))
        if self.mode == "ip_range":
            if not self.ranges:
                raise ValueError("请输入 IPv4 CIDR")
            if any(ipaddress.ip_network(value, strict=True).version != 4 for value in self.ranges):
                raise ValueError("仅支持 IPv4 CIDR")
            self.ranges = list(dict.fromkeys(self.ranges))
        return self

    def selection(self, original_country=""):
        if self.mode == "original":
            return {"type": "country", "country_code": original_country, "count": 1}
        if self.mode == "asn":
            return {"type": "asn", "asn_numbers": self.asns, "count": 1}
        if self.mode == "country":
            return {"type": "country", "country_code": self.country, "count": 1}
        if self.mode == "ip_range":
            return {"type": "ip_range", "ip_ranges": self.ranges, "count": 1}
        return dict(RANDOM)


def common_stock(snapshots):
    """Return positive inventory shared by every account in the group."""

    def normalize(snapshot):
        options = {"country": {}, "asn": {}, "ip_range": {}}
        for code, count in (snapshot.get("available_countries") or {}).items():
            if re.fullmatch(r"[A-Z]{2}", code) and type(count) is int and count > 0:
                options["country"][code] = (count, "")
        for code, item in (snapshot.get("available_asns") or {}).items():
            if (
                re.fullmatch(r"[1-9][0-9]{0,9}", code)
                and int(code) < 2**32
                and isinstance(item, (list, tuple))
                and len(item) == 2
                and type(item[1]) is int
                and item[1] > 0
            ):
                options["asn"][code] = (item[1], str(item[0])[:100])
        for prefix in (8, 16, 24):
            for cidr, count in (snapshot.get(f"available_ip_ranges_{prefix}") or {}).items():
                try:
                    network = ipaddress.IPv4Network(cidr, strict=True)
                except ValueError:
                    continue
                if network.prefixlen == prefix and type(count) is int and count > 0:
                    options["ip_range"][str(network)] = (count, "")
        return options

    normalized = [normalize(snapshot) for snapshot in snapshots]
    result = {"country": [], "asn": [], "ip_range": []}
    if not normalized:
        return result
    for kind in result:
        shared = set(normalized[0][kind]).intersection(*(set(item[kind]) for item in normalized[1:]))
        for value in sorted(shared):
            count = min(item[kind][value][0] for item in normalized)
            result[kind].append({"value": value, "count": count, "name": normalized[0][kind][value][1]})
        result[kind].sort(key=lambda item: -item["count"])
    return result


async def preflight(provider, ip, selection, job=None, save=None):
    """Only an explicit supplier stock rejection authorizes random fallback."""
    async with asyncio.timeout(15):
        if job and job.preflight_id:
            data = await provider.replacement(job.preflight_id)
        else:
            data = await provider.replace_selected(ip, selection, dry_run=True)
            if job and data.get("id"):
                job.preflight_id = str(data["id"])
                if save:
                    await save()
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
