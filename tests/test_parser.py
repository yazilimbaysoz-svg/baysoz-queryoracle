"""Lexer and parser tests — Baysöz Labs <baysozlabs@proton.me>."""

from __future__ import annotations

import pytest

from baysoz_queryoracle.ast import Aggregate, Binary, Function, Subquery, VectorSelector, tree_depth
from baysoz_queryoracle.errors import ParseError
from baysoz_queryoracle.lexer import duration_to_seconds, tokenize
from baysoz_queryoracle.parser import parse


def test_duration_conversion() -> None:
    assert duration_to_seconds("15s") == 15
    assert duration_to_seconds("1h30m") == 5400
    assert duration_to_seconds("250ms") == 0.25
    with pytest.raises(ParseError):
        duration_to_seconds("0s")
    with pytest.raises(ParseError):
        duration_to_seconds("10x")


def test_tokenizer_handles_strings_comments_and_durations() -> None:
    tokens = tokenize('rate(metric{job=~"^api$", pod!="p1"}[5m]) # gate\n')
    assert [token.kind for token in tokens].count("duration") == 1
    assert tokens[0].value == "rate"
    assert any(token.value == "^api$" and token.kind == "string" for token in tokens)
    with pytest.raises(ParseError):
        tokenize('foo{a="unterminated}')
    with pytest.raises(ParseError):
        tokenize('foo{a="\\q"}')


def test_parser_precedence_and_binary_modifiers() -> None:
    node = parse("a + b * 2 > bool 10 or c")
    assert isinstance(node, Binary)
    assert node.operator == "or"
    comparison = node.left
    assert (
        isinstance(comparison, Binary) and comparison.operator == ">" and comparison.bool_modifier
    )
    multiplication = comparison.left.right
    assert isinstance(multiplication, Binary) and multiplication.operator == "*"


def test_parser_aggregations_and_subqueries() -> None:
    grouped = parse('histogram_quantile(0.9, sum by (le) (rate(h{bucket="x"}[5m])))')
    assert isinstance(grouped, Function) and grouped.name == "histogram_quantile"
    aggregate = grouped.args[1]
    assert isinstance(aggregate, Aggregate) and aggregate.grouping == ("le",)
    subquery = parse("rate(foo[5m:1m])[1h:5m] offset 1h")
    assert isinstance(subquery, Subquery) and subquery.offset_s == 3600
    assert tree_depth(subquery) >= 3


def test_parser_accepts_recording_rule_names_and_suffix_grouping() -> None:
    selector = parse('job:http_requests:rate5m{job="api"}')
    assert isinstance(selector, VectorSelector) and selector.metric == "job:http_requests:rate5m"
    aggregate = parse("sum(rate(x[1m])) by (job)")
    assert isinstance(aggregate, Aggregate) and aggregate.grouping == ("job",)


def test_parser_rejects_invalid_forms() -> None:
    invalid = [
        "foo / group_left bar",
        "foo[5m:]",
        "topk(foo)",
        "foo{a=1}",
        "sum by(job) foo",
        "foo @ end",
    ]
    for expression in invalid:
        with pytest.raises(ParseError):
            parse(expression)
