"""Explainable house-network suitability index, not a business-success probability."""

from __future__ import annotations

import math
import re

RULE_VERSION = "rapip-house-v1"
TYPE_SOURCES = ("IPinfo", "ipapi", "AbuseIPDB", "IP2LOCATION")
REPUTATION_SOURCES = ("SCAMALYTICS", "AbuseIPDB", "IPQS")


def _score(value, percent=False):
    if isinstance(value, bool) or value is None:
        return None
    text = str(value).strip()
    if percent:
        if not text.endswith("%"):
            return None
        text = text[:-1]
    if not re.fullmatch(r"\d+(?:\.\d+)?", text):
        return None
    number = float(text)
    return number if math.isfinite(number) and 0 <= number <= 100 else None


def assess_report(report):
    """Keep dimensions distinct: low abuse does not establish residential origin."""
    scores = report.get("Score") or {}
    factor = report.get("Factor") or {}
    usage = (report.get("Type") or {}).get("Usage") or {}
    types = {"residential": [], "hosting": [], "mobile": [], "business": []}
    for source in TYPE_SOURCES:
        value = str(usage.get(source) or "").strip().lower()
        if value in {"isp", "家宽", "住宅", "residential", "line isp", "fixed line isp"}:
            types["residential"].append(source)
        elif value in {
            "hosting",
            "机房",
            "datacenter",
            "data center",
            "dch",
            "data center/web hosting/transit",
        }:
            types["hosting"].append(source)
        elif value in {"mobile", "移动", "手机", "mobile isp"}:
            types["mobile"].append(source)
        elif value in {"business", "商业", "education", "government", "commercial", "企业"}:
            types["business"].append(source)
    flags = {
        key: [source for source, value in (factor.get(key) or {}).items() if value is True]
        for key in ("Server", "Proxy", "VPN", "Tor", "Abuser", "Robot")
    }
    r, h, m = types["residential"], types["hosting"], types["mobile"]
    hosting_signals = set(h) | set(flags["Server"])
    conflict = bool(r and hosting_signals)
    if conflict:
        kind, name, fit, confidence = "mixed", "疑似伪家宽 / 来源冲突", 40, "低"
        type_reason = f"{len(r)} 个来源存在家宽标记，同时 {len(hosting_signals)} 个来源存在机房证据；可能是 ISP 代理或数据库分歧，不能据此证实造假。"
    elif h:
        kind, name, fit = "hosting", "机房 IP" if len(hosting_signals) >= 2 else "机房 IP 倾向", 20
        confidence = "高" if len(hosting_signals) >= 3 else "中" if len(hosting_signals) >= 2 else "低"
        type_reason = f"{len(h)} 个类型来源标记为机房；共 {len(hosting_signals)} 个来源提供机房正向证据。"
    elif r:
        kind, name, fit = "residential", "家宽倾向（未证实真实家庭线路）", 90
        confidence = "中" if len(r) >= 2 else "低"
        type_reason = "现有类型来源偏向 ISP/住宅，未发现机房正向标记；无法核实线路实际终端。"
    elif m:
        kind, name, fit, confidence = "mobile", "移动网络倾向", 75, "中" if len(m) >= 2 else "低"
        type_reason = "来源标记为移动网络，不等于固定家庭宽带。"
    elif hosting_signals:
        kind, name, fit, confidence = (
            "hosting",
            "机房 IP 倾向",
            20,
            "中" if len(hosting_signals) >= 2 else "低",
        )
        type_reason = "缺少明确网络类型，但存在机房属性标记。"
    elif types["business"]:
        kind, name, fit, confidence = "business", "商业 / 机构网络", 50, "低"
        type_reason = "现有来源指向商业或机构网络，无法按家宽确认。"
    else:
        kind, name, fit, confidence = "unknown", "类型未知", None, "低"
        type_reason = "没有足够的网络类型证据，未知不能当作家宽。"
    rep_values = {
        source: value for source in REPUTATION_SOURCES if (value := _score(scores.get(source))) is not None
    }
    reputation = round(100 - max(rep_values.values())) if rep_values else None
    if reputation is not None and len(rep_values) < 3:
        reputation = min(reputation, 90 if len(rep_values) >= 2 else 70)
    privacy = 100
    reasons = []
    if flags["Proxy"]:
        privacy -= 15
        reasons.append("代理标记：" + "、".join(flags["Proxy"]))
    if flags["VPN"]:
        privacy -= 20
        reasons.append("VPN 标记：" + "、".join(flags["VPN"]))
    if flags["Tor"]:
        privacy = min(privacy, 10)
        reasons.append("存在 Tor 标记")
    if flags["Abuser"] or flags["Robot"]:
        privacy = min(privacy, 30)
        reasons.append("存在滥用或机器人正向标记")
    identification_risk = _score(scores.get("IP2LOCATION"))
    if identification_risk is not None and identification_risk >= 75:
        privacy = min(privacy, 50)
        reasons.append(f"IP2Location 原始风险分 {identification_risk:g}，按高识别风险保守处理")
    flag_coverage = sum(
        any(isinstance(v, bool) for v in (factor.get(key) or {}).values())
        for key in ("Proxy", "VPN", "Tor", "Abuser", "Robot")
    )
    if flag_coverage == 0:
        privacy = None
    elif flag_coverage < 3:
        privacy = min(privacy, 70)
        reasons.append("风险标签覆盖不足 3 类，风险标签分最高 70")
    score = None
    if reputation is not None and fit is not None and privacy is not None:
        score = round(0.4 * reputation + 0.4 * fit + 0.2 * privacy)
        if kind == "hosting":
            score = min(score, 49)
        elif kind == "mixed":
            score = min(score, 64)
        if len(rep_values) < 3:
            score = min(score, 85 if len(rep_values) >= 2 else 59)
        if sum(len(values) for values in types.values()) < 2:
            score = min(score, 69)
            reasons.append("网络类型证据不足两个来源，综合分最高 69")
        if flags["Tor"]:
            score = min(score, 20)
    grade = (
        "证据不足"
        if score is None
        else "优选"
        if score >= 90
        else "较好"
        if score >= 75
        else "需要复核"
        if score >= 50
        else "谨慎使用"
    )
    if reputation is not None:
        reasons.insert(
            0, f"信誉维度 {reputation}/100；已取得 {len(rep_values)}/3 个信誉来源，缺失来源限制最高分"
        )
    if conflict:
        reasons.append("家宽与机房证据冲突，家宽场景参考分最高 64")
    if kind == "hosting":
        reasons.append("机房类型不满足严格家宽要求，家宽场景参考分最高 49")
    unknown = []
    if fit is None:
        unknown.append("网络类型")
    if reputation is None:
        unknown.append("信誉分")
    if privacy is None:
        unknown.append("风险标签")
    summary = (
        "多来源指向机房，严格家宽场景不推荐；低举报不能证明家宽。"
        if kind == "hosting"
        else "家宽与机房证据冲突，先按疑似 ISP 代理或来源分歧复核。"
        if kind == "mixed"
        else "证据不足，先补充检测信息，再判断适用场景。"
        if score is None
        else "现有证据较符合家宽要求，仍需小规模业务验证。"
        if score >= 75
        else "存在风险或证据缺口，建议复核原因后再用于高要求场景。"
    )
    return {
        "summary": summary,
        "version": RULE_VERSION,
        "score": score,
        "grade": grade,
        "profile": "严格家宽场景参考",
        "network_type": kind,
        "network_label": name,
        "type_confidence": confidence,
        "type_reason": type_reason,
        "type_sources": types,
        "hosting_sources": sorted(hosting_signals),
        "breakdown": {"reputation": reputation, "residential_fit": fit, "privacy": privacy},
        "reasons": reasons,
        "missing_dimensions": unknown,
        "formula": "信誉 40% + 家宽适配 40% + 风险标签 20%；再应用类型与缺失证据上限",
        "note": "RapIP 自定义规则指数，分数越高越适合严格家宽要求；不是注册成功率或供应商认证，不用于自动准入/替换。",
    }
