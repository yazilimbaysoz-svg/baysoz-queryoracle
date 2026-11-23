"""Deterministic lint, cost and portability analysis — Baysöz Labs <baysozlabs@proton.me>."""

from __future__ import annotations

from dataclasses import dataclass

from .ast import (
    Aggregate,
    Binary,
    Function,
    Matcher,
    Node,
    Number,
    VectorSelector,
    tree_depth,
    walk,
)
from .errors import ParseError
from .parser import parse
from .profiles import FEATURES, detect_features, unsupported_features

OFFLINE_FUNCTIONS = frozenset(
    {
        "abs",
        "avg_over_time",
        "ceil",
        "clamp",
        "clamp_max",
        "clamp_min",
        "count_over_time",
        "delta",
        "exp",
        "floor",
        "histogram_quantile",
        "idelta",
        "increase",
        "irate",
        "last_over_time",
        "ln",
        "log10",
        "log2",
        "max_over_time",
        "min_over_time",
        "rate",
        "resets",
        "round",
        "scalar",
        "sqrt",
        "sum_over_time",
        "time",
    }
)
RATE_FUNCTIONS = frozenset({"rate", "irate", "increase", "delta", "idelta"})
HIGH_CARDINALITY_LABELS = frozenset(
    {
        "pod",
        "container",
        "instance",
        "container_id",
        "uid",
        "uuid",
        "request_id",
        "trace_id",
        "client_id",
    }
)
REGEX_OPERATORS = frozenset({"=~", "!~"})

RULE_CATALOG: tuple[dict[str, str], ...] = (
    {"id": "QO001", "title": "promql_syntax_error", "severity": "error"},
    {"id": "QO002", "title": "unbounded_selector", "severity": "warning"},
    {"id": "QO003", "title": "unanchored_regex_matcher", "severity": "warning"},
    {"id": "QO004", "title": "high_cardinality_regex", "severity": "warning"},
    {"id": "QO005", "title": "rate_window_too_short", "severity": "error"},
    {"id": "QO006", "title": "histogram_quantile_without_le", "severity": "warning"},
    {"id": "QO007", "title": "expression_too_deep", "severity": "warning"},
    {"id": "QO008", "title": "unverified_backend_feature", "severity": "warning"},
    {"id": "QO009", "title": "high_cardinality_grouping", "severity": "warning"},
    {"id": "QO010", "title": "division_by_zero", "severity": "error"},
    {"id": "QO011", "title": "unknown_function", "severity": "error"},
    {"id": "QO012", "title": "function_outside_offline_oracle", "severity": "warning"},
    {"id": "QO013", "title": "empty_regex_matcher", "severity": "warning"},
    {"id": "QO016", "title": "window_relative_at_modifier", "severity": "warning"},
    {"id": "QO017", "title": "long_disjunction_chain", "severity": "warning"},
    {"id": "QO030", "title": "fixture_result_mismatch", "severity": "error"},
)
RULE_BY_ID = {rule["id"]: rule for rule in RULE_CATALOG}
SEVERITY_ORDER = {"note": 0, "warning": 1, "error": 2}


@dataclass(frozen=True, slots=True)
class Finding:
    """A single deterministic diagnostic."""

    rule_id: str
    message: str
    position: int = 0
    expression: str = ""
    profile: str = ""
    remediation: str = ""

    @property
    def severity(self) -> str:
        return RULE_BY_ID[self.rule_id]["severity"]

    @property
    def sort_key(self) -> tuple[str, int, str]:
        return (self.rule_id, self.position, self.message)

    def as_dict(self) -> dict[str, object]:
        return {
            "rule_id": self.rule_id,
            "severity": self.severity,
            "message": self.message,
            "position": self.position,
            "expression": self.expression,
            "profile": self.profile,
            "remediation": self.remediation,
        }


@dataclass(frozen=True, slots=True)
class Analysis:
    """Analysis result for one expression."""

    expression: str
    profile: str
    findings: tuple[Finding, ...]
    features: tuple[str, ...]
    cost_score: int
    cost_tier: str


def cost_tier(score: int) -> str:
    """Map an unbounded fan-out score onto a named tier."""
    if score < 20:
        return "low"
    if score < 50:
        return "moderate"
    if score < 100:
        return "elevated"
    return "severe"


def _position(node: Node) -> int:
    return getattr(node, "position", 0)


def _is_anchored(value: str) -> bool:
    return value.startswith("^") and value.endswith("$")


def _matcher_cost(matchers: tuple[Matcher, ...]) -> int:
    score = 0
    for matcher in matchers:
        if matcher.operator in REGEX_OPERATORS and not _is_anchored(matcher.value):
            score += 25
            if matcher.label in HIGH_CARDINALITY_LABELS:
                score += 20
        elif matcher.operator == "=" and matcher.label in HIGH_CARDINALITY_LABELS:
            score += 2
    return score


def analyze_node(
    node: Node,
    expression: str,
    profile: str,
    scrape_interval_s: float,
    max_depth: int,
) -> Analysis:
    """Analyze a parsed PromQL AST without any network or service dependency."""
    findings: list[Finding] = []

    def add(rule_id: str, message: str, at: int = 0, remediation: str = "") -> None:
        findings.append(Finding(rule_id, message, at, expression, profile, remediation))

    nodes = tuple(walk(node))
    score = 0
    for item in nodes:
        if isinstance(item, VectorSelector):
            matchers = item.matchers + (
                (Matcher("__name__", "=", item.metric),) if item.metric else ()
            )
            if not item.metric and not any(m.label == "__name__" for m in item.matchers):
                score += 60
                add(
                    "QO002",
                    "series selector has no metric name or __name__ matcher; scans the whole index",
                    _position(item),
                    "add a metric name or an anchored __name__ matcher",
                )
            for matcher in item.matchers:
                if matcher.operator in REGEX_OPERATORS and not matcher.value:
                    add("QO013", f"empty regex matcher on label {matcher.label!r}", _position(item))
                elif matcher.operator == "=~" and not _is_anchored(matcher.value):
                    add(
                        "QO003",
                        f"unanchored regex {matcher.value!r} on {matcher.label!r} scans labels",
                        _position(item),
                        "anchor the regex with ^...$",
                    )
                    if matcher.label in HIGH_CARDINALITY_LABELS:
                        add(
                            "QO004",
                            f"high-cardinality label {matcher.label!r} has an unanchored regex",
                            _position(item),
                            "prefer an equality matcher or an anchored, bounded regex",
                        )
            score += _matcher_cost(matchers)
            if item.at is not None and (item.at == float("inf") or item.at == float("-inf")):
                add(
                    "QO016",
                    "@ start()/end() depends on the evaluation window, not on the query alone",
                    _position(item),
                    "use an explicit numeric timestamp in CI-gated queries",
                )
        if isinstance(item, Function):
            if item.name in RATE_FUNCTIONS:
                range_ranges = [
                    arg.range_s
                    for arg in walk(item)
                    if isinstance(arg, VectorSelector) and arg.range_s is not None
                ]
                for window in range_ranges:
                    if window < scrape_interval_s:
                        add(
                            "QO005",
                            f"{item.name} window {window:g}s < scrape interval "
                            f"{scrape_interval_s:g}s",
                            _position(item),
                            "use at least two scrape intervals for counter rate windows",
                        )
                    elif window < 2 * scrape_interval_s:
                        add(
                            "QO005",
                            f"{item.name} window {window:g}s has <2 "
                            f"scrapes at {scrape_interval_s:g}s",
                            _position(item),
                            "widen the window to avoid aliasing",
                        )
            if item.name == "histogram_quantile":
                has_le_grouping = any(
                    isinstance(arg, Aggregate)
                    and (
                        not arg.without
                        and "le" in arg.grouping
                        or arg.without
                        and "le" not in arg.grouping
                    )
                    for arg in nodes
                )
                if not has_le_grouping:
                    add(
                        "QO006",
                        "histogram_quantile is not fed by an aggregation grouped by le",
                        _position(item),
                        "wrap the bucket selector in sum by (le) (...)",
                    )
            if item.name not in OFFLINE_FUNCTIONS:
                add(
                    "QO012",
                    f"{item.name} is outside the deterministic offline evaluator",
                    _position(item),
                    "limit CI-gated queries to documented supported functions",
                )
            known_functions = {rule for rule in OFFLINE_FUNCTIONS} | {
                "absent",
                "atan2",
                "info",
                "limitk",
                "limit_ratio",
                "mad_over_time",
                "sort",
                "sort_desc",
                "sort_by_label",
                "sort_by_label_desc",
                "label_replace",
                "label_join",
                "present_over_time",
                "day_of_month",
                "day_of_week",
                "days_in_month",
                "month",
                "year",
                "hour",
                "minute",
                "timestamp",
                "vector",
                "pi",
                "sgn",
                "cosh",
                "sinh",
                "tanh",
                "acosh",
                "asinh",
                "atanh",
                "deg",
                "rad",
                "double_exponential_smoothing",
                "histogram_fraction",
                "histogram_quantile",
            }
            if item.name not in known_functions:
                add(
                    "QO011",
                    f"unknown function {item.name!r}",
                    _position(item),
                    "check the function name against the target backend documentation",
                )
        if isinstance(item, Aggregate):
            if not item.without and any(
                label in HIGH_CARDINALITY_LABELS for label in item.grouping
            ):
                offenders = sorted(set(item.grouping) & HIGH_CARDINALITY_LABELS)
                add(
                    "QO009",
                    f"aggregation groups by high-cardinality labels: {', '.join(offenders)}",
                    _position(item),
                    "aggregate at service or route granularity instead",
                )
        if isinstance(item, Binary):
            if item.operator == "/" and isinstance(item.right, Number) and item.right.value == 0:
                add(
                    "QO010",
                    "literal division by zero",
                    _position(item),
                    "remove the zero denominator",
                )
            if item.operator == "or":
                chain = 1

                def count_or(current: Node) -> int:
                    if isinstance(current, Binary) and current.operator == "or":
                        return 1 + count_or(current.left)
                    return 1

                chain = count_or(item)
                if chain > 3:
                    score += (chain - 3) * 10
                    add(
                        "QO017",
                        f"disjunction chain has {chain} branches",
                        _position(item),
                        "split broad fallback chains into separate recorded queries",
                    )

    depth = tree_depth(node)
    if depth > max_depth:
        add(
            "QO007",
            f"expression nesting depth {depth} exceeds limit {max_depth}",
            0,
            "extract subexpressions into recording rules",
        )

    features = detect_features(node)
    for feature in unsupported_features(features, profile):
        add(
            "QO008",
            f"{FEATURES[feature]} is not in the verified feature set of profile {profile!r}",
            0,
            "choose a different profile or rewrite the query with core PromQL",
        )

    tier = cost_tier(score)
    if tier in {"elevated", "severe"}:
        findings.append(
            Finding(
                "QO002",
                f"estimated fan-out score {score} ({tier}) from matchers and disjunctions",
                0,
                expression,
                profile,
                "narrow selectors with equality matchers",
            )
        )
    ordered = tuple(sorted(findings, key=lambda finding: finding.sort_key))
    return Analysis(expression, profile, ordered, tuple(sorted(features)), score, tier)


def parenthesis_depth(expression: str) -> int:
    """Return maximum parenthesis nesting, including grouping parentheses."""
    depth = 0
    maximum = 0
    for character in expression:
        if character == "(":
            depth += 1
            maximum = max(maximum, depth)
        elif character == ")":
            depth = max(0, depth - 1)
    return maximum


def analyze(
    expression: str,
    profile: str = "prometheus_3",
    scrape_interval_s: float = 15.0,
    max_depth: int = 12,
) -> Analysis:
    """Parse and analyze one expression, converting syntax errors to findings."""
    try:
        node = parse(expression)
    except ParseError as error:
        finding = Finding(
            "QO001",
            error.message,
            error.position,
            expression,
            profile,
            "fix the expression syntax before gating",
        )
        return Analysis(expression, profile, (finding,), (), 0, "low")
    analysis = analyze_node(node, expression, profile, scrape_interval_s, max_depth)
    nesting = parenthesis_depth(expression)
    if nesting > max_depth:
        analysis = Analysis(
            analysis.expression,
            analysis.profile,
            analysis.findings
            + (
                Finding(
                    "QO007",
                    f"expression parenthesis depth {nesting} exceeds limit {max_depth}",
                    0,
                    expression,
                    profile,
                    "extract subexpressions into recording rules",
                ),
            ),
            analysis.features,
            analysis.cost_score,
            analysis.cost_tier,
        )
    return analysis


def worst_severity(findings: tuple[Finding, ...]) -> str:
    """Return the highest severity present in a finding set."""
    return max(
        (finding.severity for finding in findings),
        key=lambda level: SEVERITY_ORDER[level],
        default="note",
    )
