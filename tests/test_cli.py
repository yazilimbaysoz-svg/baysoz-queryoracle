"""Live CLI integration tests — Baysöz Labs <baysozlabs@proton.me>."""

from __future__ import annotations

import json

import pytest

from baysoz_queryoracle.cli import main

FIXTURE = {
    "schema": "baysoz-queryoracle/fixture-v1",
    "series": [
        {
            "labels": {"__name__": "http_requests_total", "job": "api"},
            "samples": [[0, 0], [15, 10]],
        }
    ],
}


def test_version_and_help_always_attribute_baysöz_labs(capsys) -> None:
    for arguments in (
        ["--version"],
        ["--help"],
        ["check", "--help"],
        ["eval", "--help"],
        ["verify", "--help"],
    ):
        with pytest.raises(SystemExit) as error:
            main(arguments)
        assert error.value.code == 0
        assert "baysozlabs@proton.me" in capsys.readouterr().out


def test_check_text_gate_and_sarif(capsys) -> None:
    assert main(["check", 'sum(rate(http_requests_total{job="api"}[5m]))']) == 0
    output = capsys.readouterr().out
    assert "gate: PASS" in output and "Baysöz Labs" in output
    assert main(["check", "rate(x[5s])", "--format", "sarif"]) == 1
    document = json.loads(capsys.readouterr().out)
    assert document["version"] == "2.1.0"
    assert document["runs"][0]["results"][0]["ruleId"] == "QO005"
    assert main(["check", "rate(x[5s])", "--fail-on", "none"]) == 0


def test_check_json_and_ports(capsys) -> None:
    code = main(
        [
            "check",
            "foo[5m:1m] @ 100",
            "--profile",
            "prometheus_3",
            "--target",
            "clickhouse",
            "--format",
            "json",
        ]
    )
    assert code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["attribution"] == "Baysöz Labs <baysozlabs@proton.me>"
    assert any("clickhouse" in finding["message"] for finding in payload["findings"])


def test_eval_live_output(capsys, tmp_path) -> None:
    fixture_path = tmp_path / "fixture.json"
    fixture_path.write_text(json.dumps(FIXTURE), encoding="utf-8")
    code = main(
        [
            "eval",
            'rate(http_requests_total{job="api"}[30s])',
            "--fixture",
            str(fixture_path),
            "--at",
            "15",
        ]
    )
    assert code == 0
    output = capsys.readouterr().out
    assert "resultType: vector" in output
    assert "=> 0.666667" in output


def test_verify_pack_gate(capsys, tmp_path) -> None:
    fixture_path = tmp_path / "fixture.json"
    fixture_path.write_text(json.dumps(FIXTURE), encoding="utf-8")
    pack = {
        "schema": "baysoz-queryoracle/pack-v1",
        "scrape_interval": "15s",
        "fixture": "fixture.json",
        "queries": [
            {
                "name": "request rate",
                "expr": 'rate(http_requests_total{job="api"}[30s])',
                "at": 15,
                "expect": {
                    "resultType": "vector",
                    "result": [{"metric": {"job": "api"}, "value": [15, "0.666667"]}],
                },
            }
        ],
    }
    pack_path = tmp_path / "pack.json"
    pack_path.write_text(json.dumps(pack), encoding="utf-8")
    assert main(["verify", str(pack_path)]) == 0
    assert "PASS" in capsys.readouterr().out
    pack["queries"][0]["expect"]["result"][0]["value"][1] = "9"
    pack_path.write_text(json.dumps(pack), encoding="utf-8")
    assert main(["verify", str(pack_path)]) == 1
    output = capsys.readouterr().out
    assert "QO030" in output and "value mismatch" in output


def test_profiles_and_rules(capsys) -> None:
    assert main(["profiles"]) == 0
    assert "prometheus_3:" in capsys.readouterr().out
    assert main(["rules", "--format", "json"]) == 0
    assert "QO030" in capsys.readouterr().out


def test_invalid_fixture_returns_exit_code_two(capsys, tmp_path) -> None:
    path = tmp_path / "bad.json"
    path.write_text("{}", encoding="utf-8")
    assert main(["eval", "up", "--fixture", str(path)]) == 2
    assert "baysozlabs@proton.me" in capsys.readouterr().err
