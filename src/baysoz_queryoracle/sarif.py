"""Stable SARIF 2.1.0 emission — Baysöz Labs <baysozlabs@proton.me>."""

from __future__ import annotations

import hashlib

from .rules import RULE_CATALOG, Finding

SCHEMA = "https://json.schemastore.org/sarif-2.1.0.json"


def _fingerprint(finding: Finding) -> str:
    payload = f"{finding.rule_id}|{finding.expression}|{finding.position}|{finding.message}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def build_sarif(findings: tuple[Finding, ...], version: str) -> dict[str, object]:
    """Build a deterministic SARIF document with no timestamps or host paths."""
    results: list[dict[str, object]] = []
    for finding in findings:
        rule = next(item for item in RULE_CATALOG if item["id"] == finding.rule_id)
        results.append(
            {
                "ruleId": finding.rule_id,
                "ruleIndex": next(
                    index
                    for index, item in enumerate(RULE_CATALOG)
                    if item["id"] == finding.rule_id
                ),
                "level": finding.severity,
                "message": {"text": finding.message},
                "locations": [
                    {
                        "physicalLocation": {
                            "artifactLocation": {"uri": "query.promql"},
                            "region": {"startLine": 1, "startColumn": max(1, finding.position + 1)},
                        }
                    }
                ],
                "partialFingerprints": {"baysozQueryOracle/v1": _fingerprint(finding)},
                "properties": {
                    "title": rule["title"],
                    "expression": finding.expression,
                    "profile": finding.profile,
                    "remediation": finding.remediation,
                },
            }
        )
    return {
        "$schema": SCHEMA,
        "version": "2.1.0",
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "baysoz-queryoracle",
                        "version": version,
                        "informationUri": "https://github.com/yazilimbaysoz-svg/baysoz-queryoracle",
                        "organization": "Baysöz Labs <baysozlabs@proton.me>",
                        "rules": [
                            {
                                "id": item["id"],
                                "name": item["title"],
                                "shortDescription": {"text": item["title"]},
                                "defaultConfiguration": {"level": item["severity"]},
                                "helpUri": "https://github.com/yazilimbaysoz-svg/baysoz-queryoracle#rules",
                            }
                            for item in RULE_CATALOG
                        ],
                    }
                },
                "results": results,
                "properties": {"attribution": "Baysöz Labs <baysozlabs@proton.me>"},
            }
        ],
    }
