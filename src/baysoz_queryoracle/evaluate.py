"""Offline instant-vector evaluator — Baysöz Labs <baysozlabs@proton.me>."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import TypeAlias

from .ast import (
    Aggregate,
    Binary,
    Function,
    Node,
    Number,
    String,
    Subquery,
    Unary,
    VectorSelector,
    selector_matchers,
)
from .errors import EvaluationError
from .fixtures import Fixture, PackQuery, Series
from .parser import parse
from .rules import OFFLINE_FUNCTIONS

_TOLERANCE = 1e-6


@dataclass(frozen=True, slots=True)
class Point:
    """An instant vector sample."""

    labels: dict[str, str]
    value: float


@dataclass(frozen=True, slots=True)
class RangePoint:
    """A sample inside a matrix range."""

    timestamp: float
    value: float


@dataclass(frozen=True, slots=True)
class SeriesPoints:
    """One series of range samples."""

    labels: dict[str, str]
    points: tuple[RangePoint, ...]


@dataclass(frozen=True, slots=True)
class VectorValue:
    """An instant vector result."""

    points: tuple[Point, ...] = ()


@dataclass(frozen=True, slots=True)
class ScalarValue:
    """A scalar result."""

    value: float


@dataclass(frozen=True, slots=True)
class MatrixValue:
    """A range-vector result."""

    series: tuple[SeriesPoints, ...] = ()


Value: TypeAlias = VectorValue | ScalarValue | MatrixValue


def _signature(
    labels: dict[str, str], ignore: frozenset[str] = frozenset()
) -> tuple[tuple[str, str], ...]:
    return tuple(sorted((key, value) for key, value in labels.items() if key not in ignore))


def _label_key(labels: dict[str, str]) -> str:
    return repr(_signature(labels, frozenset()))


def format_value(value: float) -> str:
    """Render a numeric value in the stable Prometheus API string form."""
    if math.isnan(value):
        return "NaN"
    if math.isinf(value):
        return "+Inf" if value > 0 else "-Inf"
    rendered = f"{value:.6f}".rstrip("0").rstrip(".")
    return "0" if rendered in {"", "-0"} else rendered


class Evaluator:
    """Evaluate a supported PromQL subset against an in-memory fixture."""

    def __init__(
        self,
        fixture: Fixture,
        at: float,
        lookback_s: float = 300.0,
        scrape_interval_s: float = 15.0,
    ) -> None:
        if not math.isfinite(at):
            raise EvaluationError("evaluation timestamp must be finite")
        if lookback_s <= 0 or scrape_interval_s <= 0:
            raise EvaluationError("lookback and scrape interval must be positive")
        self.fixture = fixture
        self.at = at
        self.lookback_s = lookback_s
        self.scrape_interval_s = scrape_interval_s

    def evaluate(self, node: Node) -> Value:
        """Evaluate an AST node at the configured instant."""
        if isinstance(node, VectorSelector):
            return self._selector(node)
        if isinstance(node, Number):
            return ScalarValue(node.value)
        if isinstance(node, String):
            raise EvaluationError("string literals are only valid as function arguments")
        if isinstance(node, Unary):
            return self._unary(node)
        if isinstance(node, Binary):
            return self._binary(node)
        if isinstance(node, Aggregate):
            return self._aggregate(node)
        if isinstance(node, Function):
            return self._function(node)
        if isinstance(node, Subquery):
            raise EvaluationError("subqueries are not evaluated by the offline oracle")
        raise EvaluationError(f"unsupported AST node {type(node).__name__}")

    def _selector(self, selector: VectorSelector) -> Value:
        matchers = selector_matchers(selector)
        effective_time = self.at - (selector.offset_s or 0.0)
        if selector.at is not None and math.isfinite(selector.at):
            effective_time = selector.at
        selected: list[Series] = []
        for series in self.fixture.series:
            if all(self._matcher_matches(matcher, series.labels) for matcher in matchers):
                selected.append(series)
        if selector.range_s is not None:
            start = effective_time - selector.range_s
            matrix = []
            for series in selected:
                points = tuple(
                    RangePoint(sample.timestamp, sample.value)
                    for sample in series.samples
                    if start < sample.timestamp <= effective_time
                )
                if points:
                    matrix.append(SeriesPoints(dict(series.labels), points))
            return MatrixValue(tuple(matrix))
        points: list[Point] = []
        for series in selected:
            eligible = [
                sample
                for sample in series.samples
                if effective_time - self.lookback_s <= sample.timestamp <= effective_time
            ]
            if eligible:
                latest = max(eligible, key=lambda sample: sample.timestamp)
                points.append(Point(dict(series.labels), latest.value))
        return VectorValue(tuple(points))

    @staticmethod
    def _matcher_matches(matcher: object, labels: dict[str, str]) -> bool:
        value = labels.get(matcher.label)
        if matcher.operator == "=":
            return value == matcher.value
        if matcher.operator == "!=":
            return value != matcher.value
        if not isinstance(matcher.value, str):
            return False
        try:
            pattern = re.compile(matcher.value)
        except re.error as error:
            raise EvaluationError(f"invalid regex matcher {matcher.value!r}: {error}") from error
        matched = value is not None and pattern.fullmatch(value) is not None
        return matched if matcher.operator == "=~" else not matched

    def _unary(self, node: Unary) -> Value:
        value = self.evaluate(node.operand)
        if isinstance(value, ScalarValue):
            return ScalarValue(-value.value)
        if isinstance(value, VectorValue):
            return VectorValue(tuple(Point(point.labels, -point.value) for point in value.points))
        raise EvaluationError("unary minus requires a vector or scalar operand")

    def _binary(self, node: Binary) -> Value:
        if node.operator in {"and", "or", "unless"}:
            return self._set_operation(node)
        left = self.evaluate(node.left)
        right = self.evaluate(node.right)
        if isinstance(left, MatrixValue) or isinstance(right, MatrixValue):
            raise EvaluationError("binary operations require instant vectors or scalars")
        if isinstance(left, ScalarValue) and isinstance(right, ScalarValue):
            return ScalarValue(
                self._arithmetic(node.operator, left.value, right.value, node.bool_modifier)
            )
        if isinstance(left, ScalarValue) or isinstance(right, ScalarValue):
            scalar = left.value if isinstance(left, ScalarValue) else right.value
            vector = right if isinstance(left, ScalarValue) else left
            if not isinstance(vector, VectorValue):
                raise EvaluationError("binary operation requires an instant vector")
            points = []
            for point in vector.points:
                a, b = (
                    (scalar, point.value)
                    if isinstance(left, ScalarValue)
                    else (point.value, scalar)
                )
                result = self._arithmetic(node.operator, a, b, node.bool_modifier)
                if not node.bool_modifier and not self._comparison_holds(node.operator, a, b):
                    continue
                points.append(Point(point.labels, result))
            return VectorValue(tuple(points))
        if not isinstance(left, VectorValue) or not isinstance(right, VectorValue):
            raise EvaluationError("binary operation requires two instant vectors")
        return self._vector_binary(node, left, right)

    def _set_operation(self, node: Binary) -> Value:
        left = self.evaluate(node.left)
        right = self.evaluate(node.right)
        if not isinstance(left, VectorValue) or not isinstance(right, VectorValue):
            raise EvaluationError(f"{node.operator} requires two instant vectors")
        left_keys = {_signature(point.labels, frozenset({"__name__"})) for point in left.points}
        right_keys = {_signature(point.labels, frozenset({"__name__"})) for point in right.points}
        if node.operator == "and":
            return VectorValue(
                tuple(
                    point
                    for point in left.points
                    if _signature(point.labels, frozenset({"__name__"})) in right_keys
                )
            )
        if node.operator == "unless":
            return VectorValue(
                tuple(
                    point
                    for point in left.points
                    if _signature(point.labels, frozenset({"__name__"})) not in right_keys
                )
            )
        return VectorValue(
            (*left.points,)
            + tuple(
                point
                for point in right.points
                if _signature(point.labels, frozenset({"__name__"})) not in left_keys
            )
        )

    def _vector_binary(self, node: Binary, left: VectorValue, right: VectorValue) -> Value:
        matching = node.matching
        kind = matching.kind if matching else "default"
        labels = matching.labels if matching else ()
        ignore: frozenset[str] = (
            frozenset({"__name__", *labels}) if kind == "ignoring" else frozenset({"__name__"})
        )

        def key(point: Point, side: str) -> tuple[tuple[str, str], ...]:
            if kind == "default" or kind == "ignoring":
                return _signature(point.labels, ignore)
            if kind == "on":
                return tuple(
                    sorted(
                        (label, point.labels[label]) for label in labels if label in point.labels
                    )
                )
            raise EvaluationError(f"unknown matching kind {kind!r}")

        left_groups: dict[tuple[tuple[str, str], ...], list[Point]] = {}
        right_groups: dict[tuple[tuple[str, str], ...], list[Point]] = {}
        for side, points, groups in (
            ("left", left.points, left_groups),
            ("right", right.points, right_groups),
        ):
            for point in points:
                groups.setdefault(key(point, side), []).append(point)
        cardinality = matching.cardinality if matching else "one-to-one"
        if cardinality == "one-to-one":
            if any(len(group) > 1 for group in (*left_groups.values(), *right_groups.values())):
                raise EvaluationError("many-to-many vector matching is not allowed")
        elif cardinality == "many-to-one":
            if any(len(group) > 1 for group in right_groups.values()):
                raise EvaluationError(
                    "group_left requires a unique right-hand series per matching group"
                )
        elif cardinality == "one-to-many":
            if any(len(group) > 1 for group in left_groups.values()):
                raise EvaluationError(
                    "group_right requires a unique left-hand series per matching group"
                )
        else:
            raise EvaluationError(f"unknown cardinality {cardinality!r}")

        results: list[Point] = []
        many_side = "right" if cardinality == "one-to-many" else "left"
        for group_key, many_points in (
            left_groups if many_side == "left" else right_groups
        ).items():
            counterparts = (right_groups if many_side == "left" else left_groups).get(group_key, [])
            if not counterparts:
                continue
            for many_point in many_points:
                for one_point in counterparts:
                    a, b = (
                        (many_point.value, one_point.value)
                        if many_side == "left"
                        else (one_point.value, many_point.value)
                    )
                    comparison = self._comparison_holds(node.operator, a, b)
                    if not node.bool_modifier and not comparison:
                        continue
                    value = self._arithmetic(node.operator, a, b, node.bool_modifier)
                    out_labels = dict(many_point.labels)
                    out_labels.pop("__name__", None)
                    if cardinality != "one-to-one" and matching and matching.include:
                        for label in matching.include:
                            if label in one_point.labels:
                                out_labels[label] = one_point.labels[label]
                    results.append(Point(out_labels, value))
        return VectorValue(tuple(results))

    @staticmethod
    def _comparison_holds(operator: str, a: float, b: float) -> bool:
        if operator in {"+", "-", "*", "/", "%", "^"}:
            return True
        return {
            "==": a == b,
            "!=": a != b,
            ">": a > b,
            "<": a < b,
            ">=": a >= b,
            "<=": a <= b,
        }[operator]

    @staticmethod
    def _arithmetic(operator: str, a: float, b: float, bool_modifier: bool) -> float:
        if bool_modifier:
            return 1.0 if Evaluator._comparison_holds(operator, a, b) else 0.0
        if operator == "+":
            return a + b
        if operator == "-":
            return a - b
        if operator == "*":
            return a * b
        if operator == "/":
            return a / b if b != 0 else math.nan
        if operator == "%":
            return math.fmod(a, b) if b != 0 else math.nan
        if operator == "^":
            return a**b
        return a if Evaluator._comparison_holds(operator, a, b) else 0.0

    def _aggregate(self, node: Aggregate) -> Value:
        value = self.evaluate(node.expr)
        if not isinstance(value, VectorValue):
            raise EvaluationError("aggregation requires an instant vector")
        params = [self.evaluate(param) for param in node.params]
        numbers = [param.value for param in params if isinstance(param, ScalarValue)]
        groups: dict[tuple[tuple[str, str], ...], list[Point]] = {}
        for point in value.points:
            if node.without:
                excluded = set(node.grouping) | {"__name__"}
                labels = {key: val for key, val in point.labels.items() if key not in excluded}
            else:
                labels = {key: point.labels[key] for key in node.grouping if key in point.labels}
            groups.setdefault(_signature(labels), []).append(point)
        results: list[Point] = []
        for signature, points in groups.items():
            labels = dict(signature)
            values = [point.value for point in points]
            operator = node.operator
            if operator == "sum":
                result = sum(values)
            elif operator == "min":
                result = min(values)
            elif operator == "max":
                result = max(values)
            elif operator == "avg":
                result = sum(values) / len(values)
            elif operator == "count":
                result = float(len(values))
            elif operator == "group":
                result = 1.0
            elif operator == "stddev":
                result = self._standard_deviation(values)
            elif operator == "stdvar":
                result = self._standard_deviation(values) ** 2
            elif operator in {"topk", "bottomk", "quantile"}:
                if not numbers:
                    raise EvaluationError(f"{operator} requires a numeric parameter")
                parameter = numbers[0]
                if operator == "quantile":
                    result = self._quantile(values, parameter)
                else:
                    count = max(0, int(parameter))
                    ordered = sorted(values, reverse=operator == "topk")[:count]
                    results.extend(Point(dict(labels), item) for item in ordered)
                    continue
            elif operator == "count_values":
                if not params or not isinstance(node.params[0], String):
                    raise EvaluationError("count_values requires a string label parameter")
                label_name = node.params[0].value
                for label_value, count in sorted(self._value_counts(values).items()):
                    output = dict(labels)
                    output[label_name] = label_value
                    results.append(Point(output, float(count)))
                continue
            else:
                raise EvaluationError(
                    f"aggregation {operator!r} is not implemented in the offline oracle"
                )
            results.append(Point(labels, result))
        return VectorValue(tuple(results))

    @staticmethod
    def _value_counts(values: list[float]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for value in values:
            key = format_value(value)
            counts[key] = counts.get(key, 0) + 1
        return counts

    @staticmethod
    def _standard_deviation(values: list[float]) -> float:
        mean = sum(values) / len(values)
        return math.sqrt(sum((value - mean) ** 2 for value in values) / len(values))

    @staticmethod
    def _quantile(values: list[float], q: float) -> float:
        if not 0 <= q <= 1:
            raise EvaluationError("quantile must be between zero and one")
        ordered = sorted(values)
        if len(ordered) == 1:
            return ordered[0]
        position = q * (len(ordered) - 1)
        lower = math.floor(position)
        upper = math.ceil(position)
        return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)

    def _function(self, node: Function) -> Value:
        if node.name not in OFFLINE_FUNCTIONS:
            raise EvaluationError(
                f"function {node.name!r} is not implemented in the offline oracle"
            )
        if node.name == "time":
            return ScalarValue(self.at)
        if node.name == "scalar":
            value = self.evaluate(node.args[0])
            if not isinstance(value, VectorValue) or len(value.points) != 1:
                raise EvaluationError("scalar() requires exactly one instant vector element")
            return ScalarValue(value.points[0].value)
        if node.name in {"rate", "irate", "increase", "delta", "idelta", "resets"}:
            return self._range_derivative(node)
        if node.name.endswith("_over_time"):
            return self._over_time(node)
        if node.name == "histogram_quantile":
            return self._histogram_quantile(node)
        if node.name == "clamp":
            value = self.evaluate(node.args[0])
            bounds = [self.evaluate(arg) for arg in node.args[1:]]
            if not all(isinstance(bound, ScalarValue) for bound in bounds) or not isinstance(
                value, VectorValue
            ):
                raise EvaluationError("clamp requires a vector and two scalar bounds")
            low, high = bounds[0].value, bounds[1].value
            return VectorValue(
                tuple(
                    Point(point.labels, min(max(point.value, low), high)) for point in value.points
                )
            )
        value = self.evaluate(node.args[0])
        if not isinstance(value, VectorValue):
            raise EvaluationError(f"{node.name} requires an instant vector")
        unary = {
            "abs": abs,
            "ceil": math.ceil,
            "floor": math.floor,
            "exp": math.exp,
            "ln": math.log,
            "log2": math.log2,
            "log10": math.log10,
            "sqrt": math.sqrt,
        }
        if node.name in unary:
            operation = unary[node.name]

            def safe(value: float) -> float:
                try:
                    return float(operation(value))
                except (ValueError, OverflowError):
                    return math.nan

            return VectorValue(
                tuple(Point(point.labels, safe(point.value)) for point in value.points)
            )
        if node.name == "round":
            return VectorValue(
                tuple(Point(point.labels, float(round(point.value))) for point in value.points)
            )
        if node.name == "clamp_min":
            bound = self.evaluate(node.args[1])
            if not isinstance(bound, ScalarValue):
                raise EvaluationError("clamp_min requires a scalar bound")
            return VectorValue(
                tuple(Point(point.labels, max(point.value, bound.value)) for point in value.points)
            )
        if node.name == "clamp_max":
            bound = self.evaluate(node.args[1])
            if not isinstance(bound, ScalarValue):
                raise EvaluationError("clamp_max requires a scalar bound")
            return VectorValue(
                tuple(Point(point.labels, min(point.value, bound.value)) for point in value.points)
            )
        raise EvaluationError(f"function {node.name!r} has no evaluator implementation")

    @staticmethod
    def _strip_name(labels: dict[str, str]) -> dict[str, str]:
        return {key: value for key, value in labels.items() if key != "__name__"}

    def _matrix(self, node: Node) -> MatrixValue:
        value = self.evaluate(node)
        if isinstance(value, VectorValue):
            return MatrixValue(
                tuple(
                    SeriesPoints(point.labels, (RangePoint(self.at, point.value),))
                    for point in value.points
                )
            )
        if isinstance(value, MatrixValue):
            return value
        raise EvaluationError(f"{node} does not produce a range vector")

    def _range_derivative(self, node: Function) -> Value:
        matrix = self._matrix(node.args[0])
        results: list[Point] = []
        for series in matrix.series:
            points = sorted(series.points, key=lambda point: point.timestamp)
            if len(points) < 2:
                continue
            if node.name == "resets":
                resets = sum(
                    1
                    for previous, current in zip(points, points[1:], strict=False)
                    if current.value < previous.value
                )
                results.append(Point(self._strip_name(series.labels), float(resets)))
                continue
            if node.name == "idelta":
                latest, previous = points[-1], points[-2]
                result = latest.value - previous.value
            elif node.name == "irate":
                latest, previous = points[-1], points[-2]
                span = latest.timestamp - previous.timestamp
                result = (
                    latest.value
                    - previous.value
                    + (previous.value if latest.value < previous.value else 0)
                ) / span
            else:
                first, last = points[0], points[-1]
                span = last.timestamp - first.timestamp
                if span <= 0:
                    continue
                resets = sum(
                    max(0.0, previous.value - current.value)
                    for previous, current in zip(points, points[1:], strict=False)
                )
                total = last.value - first.value + resets
                if node.name == "rate":
                    result = total / span
                elif node.name == "increase":
                    result = total
                elif node.name == "delta":
                    result = last.value - first.value
                else:
                    raise EvaluationError(f"unhandled range function {node.name!r}")
            results.append(Point(self._strip_name(series.labels), result))
        return VectorValue(tuple(results))

    def _over_time(self, node: Function) -> Value:
        matrix = self._matrix(node.args[0])
        operations = {
            "avg_over_time": lambda values: sum(values) / len(values),
            "sum_over_time": sum,
            "min_over_time": min,
            "max_over_time": max,
            "count_over_time": lambda values: float(len(values)),
            "last_over_time": lambda values: values[-1],
            "present_over_time": lambda values: 1.0,
        }
        operation = operations.get(node.name)
        if operation is None:
            raise EvaluationError(
                f"function {node.name!r} is not implemented in the offline oracle"
            )
        results: list[Point] = []
        for series in matrix.series:
            if not series.points:
                continue
            ordered = sorted(series.points, key=lambda point: point.timestamp)
            results.append(
                Point(
                    self._strip_name(series.labels), operation([point.value for point in ordered])
                )
            )
        return VectorValue(tuple(results))

    def _histogram_quantile(self, node: Function) -> Value:
        if len(node.args) != 2 or not isinstance(node.args[0], Number):
            raise EvaluationError(
                "histogram_quantile expects a quantile scalar and a vector of buckets"
            )
        quantile = node.args[0].value
        if not 0 <= quantile <= 1:
            raise EvaluationError("histogram_quantile quantile must be between zero and one")
        buckets = self.evaluate(node.args[1])
        if not isinstance(buckets, VectorValue):
            raise EvaluationError("histogram_quantile expects an instant vector of buckets")
        grouped: dict[tuple[tuple[str, str], ...], dict[float, float]] = {}
        for point in buckets.points:
            le = point.labels.get("le")
            if le is None:
                raise EvaluationError("histogram_quantile bucket is missing the le label")
            try:
                upper = float(le)
            except ValueError as error:
                raise EvaluationError(f"bucket le value {le!r} is not numeric") from error
            if upper == math.inf:
                continue
            key = _signature({k: v for k, v in point.labels.items() if k not in {"le", "__name__"}})
            grouped.setdefault(key, {})
            grouped[key][upper] = grouped[key].get(upper, 0.0) + point.value
        results: list[Point] = []
        for labels, by_upper in grouped.items():
            bounds = sorted(by_upper)
            cumulative = 0.0
            normalised: list[tuple[float, float]] = []
            for bound in bounds:
                cumulative = max(cumulative, by_upper[bound])
                normalised.append((bound, cumulative))
            total = cumulative
            if total <= 0:
                continue
            target = quantile * total
            lower_bound, lower_count = 0.0, 0.0
            estimate: float | None = None
            for upper_bound, upper_count in normalised:
                if upper_count >= target:
                    width = upper_bound - lower_bound
                    estimate = (
                        lower_bound + width * (target - lower_count) / (upper_count - lower_count)
                        if upper_count > lower_count
                        else 0.0
                        if width > 0
                        else upper_bound
                    )
                    break
                lower_bound, lower_count = upper_bound, upper_count
            if estimate is None:
                estimate = normalised[-1][0]
            results.append(Point(dict(labels), estimate))
        return VectorValue(tuple(results))

    def as_api(self, value: Value) -> dict[str, object]:
        """Render an evaluation result in the stable Prometheus HTTP API shape."""
        timestamp = int(self.at) if self.at.is_integer() else self.at
        if isinstance(value, ScalarValue):
            return {"resultType": "scalar", "result": [timestamp, format_value(value.value)]}
        if isinstance(value, MatrixValue):
            return {
                "resultType": "matrix",
                "result": [
                    {
                        "metric": dict(sorted(series.labels.items())),
                        "values": [
                            [int(point.timestamp), format_value(point.value)]
                            for point in series.points
                        ],
                    }
                    for series in sorted(value.series, key=lambda series: _label_key(series.labels))
                ],
            }
        return {
            "resultType": "vector",
            "result": [
                {
                    "metric": dict(sorted(point.labels.items())),
                    "value": [timestamp, format_value(point.value)],
                }
                for point in sorted(value.points, key=lambda point: _label_key(point.labels))
            ],
        }

    def verify(self, query: PackQuery) -> tuple[dict[str, object], tuple[str, ...]]:
        """Evaluate one pack query and compare against its golden result."""
        node = parse(query.expression)
        evaluator = Evaluator(
            self.fixture,
            query.at,
            scrape_interval_s=self.scrape_interval_s,
        )
        actual = evaluator.as_api(evaluator.evaluate(node))
        return actual, compare_results(actual, query.expect)


def compare_results(actual: dict[str, object], expected: dict[str, object]) -> tuple[str, ...]:
    """Compare two API-shaped results with a fixed numeric tolerance."""
    problems: list[str] = []
    if actual.get("resultType") != expected.get("resultType"):
        return (
            f"resultType mismatch: expected {expected.get('resultType')!r}, "
            f"got {actual.get('resultType')!r}",
        )
    if actual["resultType"] == "scalar":
        expected_value = _expect_number(expected["result"])
        actual_value = _expect_number(actual["result"])
        if not math.isclose(actual_value, expected_value, rel_tol=_TOLERANCE, abs_tol=_TOLERANCE):
            problems.append(f"scalar mismatch: expected {expected_value:g}, got {actual_value:g}")
        return tuple(problems)
    actual_points = _index_points(actual["result"])
    expected_points = _index_points(expected["result"])
    for metric in sorted(expected_points):
        if metric not in actual_points:
            problems.append(f"missing series {metric}")
        elif not math.isclose(
            actual_points[metric], expected_points[metric], rel_tol=_TOLERANCE, abs_tol=_TOLERANCE
        ):
            problems.append(
                f"value mismatch {metric}: expected {expected_points[metric]:g}, "
                f"got {actual_points[metric]:g}"
            )
    for metric in sorted(actual_points):
        if metric not in expected_points:
            problems.append(f"unexpected series {metric}")
    return tuple(problems)


def _expect_number(value: object) -> float:
    if not isinstance(value, list) or len(value) != 2:
        raise EvaluationError("malformed API result: expected [timestamp, value]")
    try:
        return float(value[1])
    except (TypeError, ValueError) as error:
        raise EvaluationError("non-numeric result value") from error


def _index_points(result: object) -> dict[str, float]:
    if not isinstance(result, list):
        raise EvaluationError("malformed API result: result must be an array")
    points: dict[str, float] = {}
    for item in result:
        if not isinstance(item, dict) or not isinstance(item.get("metric"), dict):
            raise EvaluationError("malformed API result: missing metric labels")
        labels = item["metric"]
        if not all(
            isinstance(key, str) and isinstance(value, str) for key, value in labels.items()
        ):
            raise EvaluationError("malformed API result: label values must be strings")
        points[_label_key(labels)] = _expect_number(item.get("value"))
    return points
