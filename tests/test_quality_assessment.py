from rapip.quality_assessment import assess_report


def fixture(types, **scores):
    return {
        "Type": {"Usage": types},
        "Score": {"SCAMALYTICS": "0", "AbuseIPDB": "0", **scores},
        "Factor": {"Proxy": {"IPinfo": False}, "VPN": {"IPinfo": False}, "Server": {"IPinfo": False}},
    }


def test_hosting_with_low_abuse_does_not_become_high_quality_residential():
    report = fixture(
        {source: "机房" for source in ("IPinfo", "ipapi", "AbuseIPDB", "IP2LOCATION")}, IP2LOCATION="91"
    )
    report["Factor"]["VPN"]["IP2LOCATION"] = True
    a = assess_report(report)
    assert a["network_type"] == "hosting" and a["type_confidence"] == "高"
    assert a["breakdown"]["reputation"] == 90 and a["score"] == 49
    assert a["grade"] == "谨慎使用" and a["profile"] == "严格家宽场景参考"


def test_conflicting_residential_labels_are_suspected_not_proven_fake():
    r = fixture({"IPinfo": "家宽", "ipapi": "家宽", "AbuseIPDB": "家宽", "IP2LOCATION": "机房"})
    a = assess_report(r)
    assert a["network_type"] == "mixed" and "疑似" in a["network_label"]
    assert a["score"] <= 64 and a["type_confidence"] == "低"
    assert "不能据此证实造假" in a["type_reason"]


def test_consistent_residential_and_full_reputation_can_score_high():
    a = assess_report(fixture({"IPinfo": "ISP", "ipapi": "家宽"}, IPQS="0"))
    assert a["network_type"] == "residential" and a["score"] >= 90
    assert "未证实" in a["network_label"] and a["grade"] == "优选"


def test_missing_evidence_never_defaults_to_zero_or_full_score():
    a = assess_report({})
    assert a["score"] is None and a["grade"] == "证据不足"
    assert set(a["missing_dimensions"]) == {"网络类型", "信誉分", "风险标签"}
    r = fixture({"IPinfo": "ISP", "ipapi": "ISP"})
    r["Score"] = {"AbuseIPDB": "null", "IPQS": True, "SCAMALYTICS": "999"}
    assert assess_report(r)["score"] is None


def test_partial_sources_and_tor_are_capped_and_reasons_visible():
    r = fixture({"IPinfo": "ISP", "ipapi": "ISP"})
    assert assess_report(r)["score"] <= 85
    r["Factor"]["Tor"] = {"IPinfo": True}
    a = assess_report(r)
    assert a["score"] <= 20 and any("Tor" in reason for reason in a["reasons"])


async def test_cached_legacy_report_is_scored_without_probe_or_mutation(client, gateway, monkeypatch):
    import copy
    import time

    node = next(iter(gateway.nodes.values()))
    report = {
        **fixture({"IPinfo": "机房", "ipapi": "机房"}, IP2LOCATION="91"),
        "ip": node.ip,
        "checked_at": time.time(),
    }
    gateway.ipquality.cache[node.id] = report
    before = copy.deepcopy(report)

    async def fail(*args):
        raise AssertionError("GET must not start another network diagnostic")

    monkeypatch.setattr(gateway.ipquality, "execute", fail)
    response = await client.get(
        f"/api/v1/nodes/{node.id}/ip-quality", headers={"Authorization": "Bearer test-api-token"}
    )
    assert response.status_code == 200
    assert response.json()["report"]["assessment"]["score"] <= 49
    assert report == before and gateway.test_provider.calls == 0
