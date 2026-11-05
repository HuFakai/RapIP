"""Supplier snapshots, kept distinct from local replacement safety budgets."""

from __future__ import annotations

import math
import time
from datetime import datetime, timezone

STALE_AFTER = 900
GB = 1_000_000_000


def number(value, integer=False):
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value < 0
    ):
        return None
    return int(value) if not integer or value == int(value) else None


def timestamp(value):
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except (ValueError, AttributeError, TypeError):
        raise ValueError("invalid subscription period") from None


def normalize_usage(subscription, plan, stats, now=None):
    now = time.time() if now is None else now
    if not isinstance(subscription, dict) or not isinstance(plan, dict) or not isinstance(stats, dict):
        raise ValueError("invalid supplier usage")
    begin, end = timestamp(subscription.get("start_date")), timestamp(subscription.get("end_date"))
    if not begin.timestamp() <= now < end.timestamp():
        raise ValueError("subscription period is not current")
    limit = plan.get("bandwidth_limit")
    if (
        isinstance(limit, bool)
        or not isinstance(limit, (int, float))
        or not math.isfinite(limit)
        or limit < 0
    ):
        limit = None
    used = number(stats.get("bandwidth_total"))
    total = int(limit * GB) if limit is not None and limit > 0 else None
    unlimited = limit == 0
    replacements = {
        key: number(plan.get("proxy_replacements_" + source), integer=True)
        for key, source in (("total", "total"), ("used", "used"), ("remaining", "available"))
    }
    if (
        replacements["total"] is not None
        and replacements["remaining"] is not None
        and replacements["remaining"] > replacements["total"]
    ):
        replacements = {"total": None, "remaining": None, "used": None}
    return {
        "source": "webshare_api",
        "subscription_id": str(subscription.get("id", "")),
        "plan_id": str(plan["id"]),
        "period_start": begin.isoformat(),
        "period_end": end.isoformat(),
        "checked_at": now,
        "last_attempt": now,
        "error": None,
        "replacement": replacements,
        "traffic": {
            "total_bytes": total,
            "used_bytes": used,
            "remaining_bytes": max(0, total - used) if total is not None and used is not None else None,
            "unlimited": unlimited,
            "limit_gb": limit,
            "unit": "GB",
            "bytes_per_gb": GB,
            "basis": "供应商当前订阅周期累计；小时聚合可能延迟",
        },
    }


def public_usage(snapshot, now=None):
    now = time.time() if now is None else now
    data = dict(snapshot or {})
    expired = False
    if data.get("period_end"):
        expired = timestamp(data["period_end"]).timestamp() <= now
    data["stale"] = bool(data.get("error")) or now - data.get("checked_at", 0) > STALE_AFTER or expired
    data["period_expired"] = expired
    if expired:
        data["replacement"] = {"total": None, "remaining": None, "used": None}
        data["traffic"] = {
            "total_bytes": None,
            "remaining_bytes": None,
            "used_bytes": None,
            "unlimited": False,
            "limit_gb": None,
        }
        data["error"] = "订阅周期已结束，等待同步新周期"
    return data


def aggregate_usage(accounts, now=None):
    now = time.time() if now is None else now
    seen, duplicate, missing = set(), 0, 0
    result = {
        "account_count": len(accounts),
        "unique_plans": 0,
        "duplicate_accounts": 0,
        "replacement": {"total": 0, "remaining": 0, "covered_plans": 0},
        "traffic": {
            "total_bytes": 0,
            "remaining_bytes": 0,
            "used_bytes": 0,
            "unlimited_plans": 0,
            "covered_plans": 0,
        },
        "oldest_checked_at": None,
        "missing_accounts": 0,
    }
    for account in accounts:
        snap = public_usage(account.get("supplier_usage"), now)
        if snap.get("stale") or not snap.get("plan_id"):
            missing += 1
            continue
        identity = (snap.get("subscription_id"), snap["plan_id"])
        if identity in seen:
            duplicate += 1
            continue
        seen.add(identity)
        result["unique_plans"] += 1
        result["oldest_checked_at"] = min(result["oldest_checked_at"] or now, snap["checked_at"])
        rep, traffic = snap.get("replacement", {}), snap.get("traffic", {})
        if rep.get("remaining") is not None and rep.get("total") is not None:
            result["replacement"]["total"] += rep["total"]
            result["replacement"]["remaining"] += rep["remaining"]
            result["replacement"]["covered_plans"] += 1
        if traffic.get("unlimited"):
            result["traffic"]["unlimited_plans"] += 1
            result["traffic"]["covered_plans"] += 1
        elif traffic.get("remaining_bytes") is not None and traffic.get("total_bytes") is not None:
            for key in ("total_bytes", "remaining_bytes", "used_bytes"):
                result["traffic"][key] += traffic[key]
            result["traffic"]["covered_plans"] += 1
    result.update(duplicate_accounts=duplicate, missing_accounts=missing)
    for key in ("replacement", "traffic"):
        dimension = result[key]
        dimension["complete"] = dimension["covered_plans"] == len(seen) and missing == 0 and bool(seen)
        if not dimension["covered_plans"]:
            for field in (
                ("total", "remaining")
                if key == "replacement"
                else ("total_bytes", "remaining_bytes", "used_bytes")
            ):
                dimension[field] = None
    return result
