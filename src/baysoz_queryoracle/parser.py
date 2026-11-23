"""Deterministic PromQL parser — Baysöz Labs <baysozlabs@proton.me>."""

from __future__ import annotations

import math

from .ast import (
    MATCH_OPERATORS,
    Aggregate,
    Binary,
    Function,
    Matching,
    Matcher,
    Node,
    Number,
    String,
    Subquery,
    Unary,
    VectorSelector,
)
from .errors import ParseError
from .lexer import Token, duration_to_seconds, tokenize

AGGREGATIONS = frozenset(
    {
        "sum",
        "min",
        "max",
        "avg",
        "count",
        "group",
        "stddev",
        "stdvar",
        "topk",
        "bottomk",
        "quantile",
        "count_values",
        "limitk",
        "limit_ratio",
    }
)
AGGREGATIONS_WITH_PARAM = frozenset(
    {"topk", "bottomk", "quantile", "count_values", "limitk", "limit_ratio"}
)
COMPARISONS = frozenset({"==", "!=", ">", "<", ">=", "<="})
SET_OPERATORS = frozenset({"and", "or", "unless"})
BINARY_PRECEDENCE = {
    "or": 1,
    "unless": 2,
    "and": 3,
    **{op: 4 for op in COMPARISONS},
    "+": 5,
    "-": 5,
    "*": 6,
    "/": 6,
    "%": 6,
    "^": 7,
}
_GROUPING_KEYWORDS = frozenset({"by", "without"})
_MODIFIER_KEYWORDS = frozenset({"bool", "on", "ignoring", "group_left", "group_right", "offset"})
_RESERVED = _GROUPING_KEYWORDS | _MODIFIER_KEYWORDS | SET_OPERATORS


class Parser:
    """Recursive-descent, precedence-climbing PromQL parser."""

    def __init__(self, source: str) -> None:
        self.source = source
        self.tokens = tokenize(source)
        self.index = 0

    def parse(self) -> Node:
        """Parse the complete expression."""
        node = self.parse_expression(1)
        if self.peek().kind != "eof":
            token = self.peek()
            raise ParseError(f"unexpected token {token.value!r}", token.position)
        return node

    def peek(self, ahead: int = 0) -> Token:
        return self.tokens[min(self.index + ahead, len(self.tokens) - 1)]

    def take(self) -> Token:
        token = self.peek()
        if token.kind != "eof":
            self.index += 1
        return token

    def at_value(self, value: str) -> bool:
        token = self.peek()
        return token.value == value and token.kind in {"ident", "op", "punct"}

    def accept(self, value: str) -> bool:
        if self.at_value(value):
            self.take()
            return True
        return False

    def expect(self, value: str) -> Token:
        if not self.at_value(value):
            raise ParseError(
                f"expected {value!r}, found {self.peek().value!r}", self.peek().position
            )
        return self.take()

    def expect_ident(self) -> str:
        token = self.peek()
        if token.kind != "ident" or token.value in _RESERVED:
            raise ParseError(f"expected identifier, found {token.value!r}", token.position)
        self.take()
        return token.value

    def parse_expression(self, minimum_precedence: int) -> Node:
        left = self.parse_unary()
        while True:
            token = self.peek()
            precedence = BINARY_PRECEDENCE.get(token.value)
            if (
                precedence is None
                or precedence < minimum_precedence
                or token.kind not in {"op", "ident"}
            ):
                break
            operator = self.take().value
            if operator in SET_OPERATORS and precedence < minimum_precedence:
                break
            bool_modifier = False
            matching = None
            if operator in COMPARISONS and self.at_value("bool"):
                self.take()
                bool_modifier = True
            if self.at_value("on") or self.at_value("ignoring"):
                kind = self.take().value
                matching = Matching(kind, self.parse_label_list())
                if self.at_value("group_left") or self.at_value("group_right"):
                    cardinality = (
                        "many-to-one" if self.take().value == "group_left" else "one-to-many"
                    )
                    include = self.parse_label_list() if self.at_value("(") else ()
                    matching = Matching(kind, matching.labels, cardinality, include)
            elif self.at_value("group_left") or self.at_value("group_right"):
                raise ParseError(
                    "group_left/group_right requires on() or ignoring()", self.peek().position
                )
            if bool_modifier and operator not in COMPARISONS:
                raise ParseError("bool modifier is only valid for comparisons", token.position)
            next_minimum = precedence if operator == "^" else precedence + 1
            right = self.parse_expression(next_minimum)
            left = Binary(operator, left, right, bool_modifier, matching)
        return left

    def parse_unary(self) -> Node:
        token = self.peek()
        if token.kind == "op" and token.value in {"+", "-"}:
            self.take()
            operand = self.parse_expression(BINARY_PRECEDENCE["^"])
            if token.value == "+":
                return operand
            return Unary("-", operand)
        return self.parse_postfix()

    def parse_postfix(self) -> Node:
        node = self.parse_primary()
        while True:
            if self.at_value("["):
                self.take()
                range_s = self.parse_duration()
                if self.at_value(":"):
                    self.take()
                    if not self.at_value("]"):
                        step_s = self.parse_duration()
                    else:
                        raise ParseError("subquery requires an explicit step", self.peek().position)
                    self.expect("]")
                    node = Subquery(node, range_s, step_s)
                else:
                    self.expect("]")
                    if not isinstance(node, VectorSelector):
                        raise ParseError(
                            "range selector requires a vector selector", self.peek().position
                        )
                    node = VectorSelector(
                        node.metric, node.matchers, range_s, node.offset_s, node.at
                    )
                continue
            if self.at_value("offset"):
                self.take()
                sign = -1.0 if self.accept("-") else 1.0
                offset_s = sign * self.parse_duration()
                if isinstance(node, Subquery):
                    node = Subquery(node.expr, node.range_s, node.step_s, offset_s, node.at)
                elif isinstance(node, VectorSelector):
                    node = VectorSelector(
                        node.metric, node.matchers, node.range_s, offset_s, node.at
                    )
                else:
                    raise ParseError(
                        "offset requires a vector selector or subquery", self.peek().position
                    )
                continue
            if self.at_value("@"):
                self.take()
                at = self.parse_timestamp()
                if isinstance(node, Subquery):
                    node = Subquery(node.expr, node.range_s, node.step_s, node.offset_s, at)
                elif isinstance(node, VectorSelector):
                    node = VectorSelector(
                        node.metric, node.matchers, node.range_s, node.offset_s, at
                    )
                else:
                    raise ParseError(
                        "@ modifier requires a vector selector or subquery", self.peek().position
                    )
                continue
            return node

    def parse_duration(self) -> float:
        token = self.peek()
        if token.kind != "duration":
            raise ParseError("expected duration literal", token.position)
        self.take()
        return duration_to_seconds(token.value, token.position)

    def parse_timestamp(self) -> float:
        token = self.peek()
        if token.kind == "number":
            self.take()
            return float(token.value)
        if token.kind == "ident" and token.value in {"start", "end"} and self.peek(1).value == "(":
            self.take()
            self.expect("(")
            self.expect(")")
            return -math.inf if token.value == "start" else math.inf
        raise ParseError("expected timestamp, start() or end()", token.position)

    def parse_label_list(self) -> tuple[str, ...]:
        if not self.at_value("("):
            return ()
        self.take()
        labels: list[str] = []
        if not self.at_value(")"):
            while True:
                labels.append(self.expect_ident())
                if not self.accept(","):
                    break
        self.expect(")")
        return tuple(labels)

    def parse_primary(self) -> Node:
        token = self.peek()
        if token.kind == "number":
            self.take()
            return Number(float(token.value))
        if token.kind == "string":
            self.take()
            return String(token.value)
        if self.at_value("("):
            self.take()
            node = self.parse_expression(1)
            self.expect(")")
            return node
        if self.at_value("{"):
            return VectorSelector("", self.parse_matchers())
        if token.kind == "ident" and token.value not in _RESERVED:
            name = self.take().value
            if self.at_value("(") or (
                name in AGGREGATIONS and (self.at_value("by") or self.at_value("without"))
            ):
                return self.parse_call(name)
            matchers = self.parse_matchers() if self.at_value("{") else ()
            return VectorSelector(name, matchers)
        raise ParseError(f"unexpected token {token.value!r}", token.position)

    def parse_call(self, name: str) -> Node:
        grouping: tuple[str, ...] = ()
        without = False
        if self.at_value("by") or self.at_value("without"):
            without = self.take().value == "without"
            grouping = self.parse_label_list()
        self.expect("(")
        args: list[Node] = []
        if not self.at_value(")"):
            args.append(self.parse_expression(1))
            while self.accept(","):
                if self.at_value(")"):
                    break
                args.append(self.parse_expression(1))
        self.expect(")")
        if not grouping and (self.at_value("by") or self.at_value("without")):
            without = self.peek().value == "without"
            self.take()
            grouping = self.parse_label_list()
        if name in AGGREGATIONS:
            if name in AGGREGATIONS_WITH_PARAM:
                if len(args) < 2:
                    raise ParseError(
                        f"{name} requires a parameter and an expression", self.peek().position
                    )
                return Aggregate(name, args[-1], tuple(args[:-1]), grouping, without)
            if len(args) != 1:
                raise ParseError(f"{name} accepts exactly one expression", self.peek().position)
            return Aggregate(name, args[0], (), grouping, without)
        return Function(name, tuple(args))

    def parse_matchers(self) -> tuple[Matcher, ...]:
        self.expect("{")
        matchers: list[Matcher] = []
        if not self.at_value("}"):
            while True:
                label = self.expect_ident()
                operator_token = self.peek()
                if operator_token.value not in MATCH_OPERATORS:
                    raise ParseError("expected a label matcher operator", operator_token.position)
                self.take()
                value_token = self.peek()
                if value_token.kind != "string":
                    raise ParseError("expected a quoted matcher value", value_token.position)
                self.take()
                matchers.append(Matcher(label, operator_token.value, value_token.value))
                if not self.accept(","):
                    break
        self.expect("}")
        return tuple(matchers)


def parse(source: str) -> Node:
    """Parse one PromQL expression."""
    return Parser(source).parse()
