"""Rule, profile and SARIF tests — Baysöz Labs <baysozlabs@proton.me>."""

from __future__ import annotations

from baysoz_queryoracle.parser import parse
from baysoz_queryoracle.profiles import detect_features, unsupported_features
from baysoz_queryoracle.rules import analyze, worst_severity
from baysoz_queryoracle.sarif import build_sarif


def test_clean_query_has_no_findings() -> None:
    analysis = analyze('sum by (job) (rate(http_requests_total{job="api"}[5m]))')
    assert analysis.findings == ()
    assert analysis.cost_tier == "low"


def test_cost_and_cardinality_rules() -> None:
    analysis = analyze('count({__name__=~".+"}) or {pod=~".*"} or {instance=~".*"} or {uid=~".*"}')
    ids = {finding.rule_id for finding in analysis.findings}
    assert {"QO002", "QO003", "QO004", "QO017"} <= ids
    assert analysis.cost_score >= 100


def test_rate_window_hard_error() -> None:
    assert any(f.rule_id == "QO005" for f in analyze("rate(x[5s])").findings)
    assert not any(f.rule_id == "QO005" for f in analyze("rate(x[5m])").findings)


def test_histogram_division_and_nesting_rules() -> None:
    assert any(f.rule_id == "QO006" for f in analyze("histogram_quantile(0.9, x)").findings)
    assert any(f.rule_id == "QO010" for f in analyze("x / 0").findings)
    assert any(f.rule_id == "QO007" for f in analyze("((((((a))))))", max_depth=4).findings)


def test_syntax_error_is_a_finding_not_a_crash() -> None:
    analysis = analyze("rate(")
    assert len(analysis.findings) == 1
    assert analysis.findings[0].rule_id == "QO001"
    assert worst_severity(analysis.findings) == "error"


def test_portability_profiles_are_conservative() -> None:
    node = parse("foo[5m:1m] @ 100")
    features = detect_features(node)
    assert {"at_modifier", "subquery"} <= features
    assert unsupported_features(features, "prometheus_3") == ()
    assert "subquery" in unsupported_features(features, "clickhouse")
    assert any(
        f.rule_id == "QO008" and "clickhouse" in f.message
        for f in analyze("foo[5m:1m] @ 100", profile="clickhouse").findings
    )


def test_sarif_is_stable_and_has_fingerprints() -> None:
    findings = analyze('count({__name__=~".+"})').findings
    first = build_sarif(findings, "1.0.0")
    second = build_sarif(findings, "1.0.0")
    assert first == second
    assert first["version"] == "2.1.0"
    assert (
        first["runs"][0]["tool"]["driver"]["organization"] == "Baysöz Labs <baysozlabs@proton.me>"
    )
    assert "partialFingerprints" in first["runs"][0]["results"][0]
