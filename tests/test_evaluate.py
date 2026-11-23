"""Offline evaluator and fixture tests — Baysöz Labs <baysozlabs@proton.me>."""

from __future__ import annotations

import json

import pytest

from baysoz_queryoracle.errors import EvaluationError, FixtureError
from baysoz_queryoracle.evaluate import Evaluator, compare_results, format_value
from baysoz_queryoracle.fixtures import fixture_from_dict, load_fixture, load_pack
from baysoz_queryoracle.parser import parse
from baysoz_queryoracle.rules import analyze

FIXTURE = {
    "schema": "baysoz-queryoracle/fixture-v1",
    "series": [
        {
            "labels": {"__name__": "http_requests_total", "job": "api", "instance": "a"},
            "samples": [[0, 0], [15, 10], [30, 25], [45, 40]],
        },
        {
            "labels": {"__name__": "http_requests_total", "job": "api", "instance": "b"},
            "samples": [[0, 0], [15, 5], [30, 5], [45, 8]],
        },
        {
            "labels": {"__name__": "http_errors_total", "job": "api", "instance": "a"},
            "samples": [[0, 0], [15, 1], [30, 4], [45, 4]],
        },
        {
            "labels": {"__name__": "resets_total", "job": "worker"},
            "samples": [[0, 0], [15, 10], [30, 2], [45, 5]],
        },
        {
            "labels": {"__name__": "build_info", "job": "api", "version": "v2"},
            "samples": [[0, 1], [45, 1]],
        },
        *[
            {
                "labels": {"__name__": "duration_bucket", "job": "api", "le": le},
                "samples": [[0, 0], [15, count]],
            }
            for le, count in (("0.1", 5), ("0.5", 9), ("1", 10), ("+Inf", 10))
        ],
    ],
}


def evaluator(at: float = 45.0) -> Evaluator:
    return Evaluator(fixture_from_dict(FIXTURE), at, scrape_interval_s=15.0)


def test_rate_counter_reset_and_aggregation() -> None:
    result = evaluator().as_api(evaluator().evaluate(parse("sum(rate(http_requests_total[30s]))")))
    assert result["resultType"] == "vector"
    assert result["result"][0]["value"][1] == "1.2"


def test_increase_and_delta_differ_on_resets() -> None:
    engine = evaluator(45.0)
    increase = engine.as_api(engine.evaluate(parse("increase(resets_total[45s])")))
    delta = engine.as_api(engine.evaluate(parse("delta(resets_total[45s])")))
    assert increase["result"][0]["value"][1] == "3"
    assert delta["result"][0]["value"][1] == "-5"
    assert any(f.rule_id == "QO005" for f in analyze("rate(resets_total[15s])").findings)


def test_over_time_and_scalar_functions() -> None:
    engine = evaluator()
    result = engine.as_api(engine.evaluate(parse("max_over_time(http_requests_total[1m])")))
    assert {item["metric"]["instance"] for item in result["result"]} == {"a", "b"}
    scalar = engine.as_api(engine.evaluate(parse('scalar(http_requests_total{instance="a"})')))
    assert scalar["result"][1] == "40"
    assert engine.as_api(engine.evaluate(parse("time()")))["result"][1] == "45"


def test_histogram_quantile_interpolation() -> None:
    engine = evaluator(15.0)
    result = engine.as_api(
        engine.evaluate(
            parse("histogram_quantile(0.9, sum by (le) (increase(duration_bucket[30s])))")
        )
    )
    assert result["result"][0]["value"][1] == "0.5"


def test_binary_matching_and_set_operations() -> None:
    engine = evaluator(45.0)
    divided = engine.as_api(
        engine.evaluate(parse("http_requests_total / on(instance) http_errors_total"))
    )
    assert divided["result"]
    grouped = engine.as_api(
        engine.evaluate(
            parse('http_requests_total{instance="a"} / on(job) group_left(version) build_info')
        )
    )
    assert grouped["resultType"] == "vector"
    assert grouped["result"][0]["metric"]["version"] == "v2"
    only_requests = engine.as_api(
        engine.evaluate(parse("http_requests_total unless http_errors_total"))
    )
    assert all("__name__" in item["metric"] for item in only_requests["result"])


def test_aggregations_and_comparisons() -> None:
    engine = evaluator()
    top = engine.as_api(engine.evaluate(parse("topk(1, rate(http_requests_total[30s]))")))
    assert len(top["result"]) == 1
    comparison = engine.as_api(engine.evaluate(parse("rate(http_requests_total[30s]) > bool 1")))
    assert all(item["value"][1] in {"0", "1"} for item in comparison["result"])
    counts = engine.as_api(engine.evaluate(parse("count by (job) (http_requests_total)")))
    assert counts["result"][0]["value"][1] == "2"


def test_evaluator_errors_are_explicit() -> None:
    engine = evaluator()
    with pytest.raises(EvaluationError):
        engine.evaluate(parse("subquery_unsupported(x[5m:1m])"))
    with pytest.raises(EvaluationError):
        evaluator(float("nan"))
    with pytest.raises(EvaluationError):
        engine.evaluate(parse('http_requests_total{a=~"["}'))


def test_compare_results_detects_drift() -> None:
    actual = {"resultType": "vector", "result": [{"metric": {"job": "api"}, "value": [45, "1.2"]}]}
    expected = {
        "resultType": "vector",
        "result": [{"metric": {"job": "api"}, "value": [45, "1.2"]}],
    }
    assert compare_results(actual, expected) == ()
    expected["result"][0]["value"][1] = "2.2"
    assert any("value mismatch" in problem for problem in compare_results(actual, expected))


def test_format_value_is_stable() -> None:
    assert format_value(1.0) == "1"
    assert format_value(-0.0) == "0"
    assert format_value(float("inf")) == "+Inf"


def test_fixture_validation_is_strict(tmp_path) -> None:
    with pytest.raises(FixtureError):
        fixture_from_dict({"schema": "wrong"})
    with pytest.raises(FixtureError):
        fixture_from_dict({"schema": "baysoz-queryoracle/fixture-v1", "series": []})
    duplicated = json.loads(json.dumps(FIXTURE))
    duplicated["series"].append(duplicated["series"][0])
    with pytest.raises(FixtureError):
        fixture_from_dict(duplicated)
    invalid_labels = json.loads(json.dumps(FIXTURE))
    invalid_labels["series"][0]["labels"]["bad-label"] = "x"
    with pytest.raises(FixtureError):
        fixture_from_dict(invalid_labels)


def test_load_fixture_and_pack(tmp_path) -> None:
    fixture_path = tmp_path / "fixture.json"
    fixture_path.write_text(json.dumps(FIXTURE), encoding="utf-8")
    assert len(load_fixture(fixture_path).series) == len(FIXTURE["series"])
    pack_path = tmp_path / "pack.json"
    pack_path.write_text(
        json.dumps(
            {
                "schema": "baysoz-queryoracle/pack-v1",
                "scrape_interval": "15s",
                "fixture": "fixture.json",
                "queries": [
                    {
                        "name": "request rate",
                        "expr": "sum(rate(http_requests_total[30s]))",
                        "at": 45,
                        "expect": {
                            "resultType": "vector",
                            "result": [{"metric": {}, "value": [45, "1.2"]}],
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    pack = load_pack(pack_path)
    assert pack.scrape_interval_s == 15.0
    engine = Evaluator(pack.fixture, pack.queries[0].at, scrape_interval_s=pack.scrape_interval_s)
    actual, problems = engine.verify(pack.queries[0])
    assert problems == ()
    assert actual["result"][0]["value"][1] == "1.2"
