"""Immutable PromQL AST — Baysöz Labs <baysozlabs@proton.me>."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator, TypeAlias

MATCH_OPERATORS = frozenset({"=", "!=", "=~", "!~"})


@dataclass(frozen=True, slots=True)
class Matcher:
    """A single label matcher inside a vector selector."""

    label: str
    operator: str
    value: str


@dataclass(frozen=True, slots=True)
class VectorSelector:
    """An instant or range vector selector."""

    metric: str
    matchers: tuple[Matcher, ...] = ()
    range_s: float | None = None
    offset_s: float | None = None
    at: float | None = None


@dataclass(frozen=True, slots=True)
class Number:
    """A numeric literal."""

    value: float


@dataclass(frozen=True, slots=True)
class String:
    """A string literal."""

    value: str


@dataclass(frozen=True, slots=True)
class Unary:
    """A unary arithmetic operation."""

    operator: str
    operand: Node


@dataclass(frozen=True, slots=True)
class Matching:
    """Vector matching modifiers of a binary operation."""

    kind: str
    labels: tuple[str, ...] = ()
    cardinality: str = "one-to-one"
    include: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Binary:
    """A binary operation between two expressions."""

    operator: str
    left: Node
    right: Node
    bool_modifier: bool = False
    matching: Matching | None = None


@dataclass(frozen=True, slots=True)
class Function:
    """A PromQL function call."""

    name: str
    args: tuple[Node, ...]


@dataclass(frozen=True, slots=True)
class Aggregate:
    """An aggregation operator with optional grouping."""

    operator: str
    expr: Node
    params: tuple[Node, ...] = ()
    grouping: tuple[str, ...] = ()
    without: bool = False


@dataclass(frozen=True, slots=True)
class Subquery:
    """A subquery expression such as ``rate(x[5m])[1h:1m]``."""

    expr: Node
    range_s: float
    step_s: float
    offset_s: float | None = None
    at: float | None = None


Node: TypeAlias = (
    VectorSelector | Number | String | Unary | Binary | Function | Aggregate | Subquery
)


def children(node: Node) -> tuple[Node, ...]:
    """Return direct AST children in deterministic order."""
    if isinstance(node, (Unary, Subquery)):
        return (node.operand if isinstance(node, Unary) else node.expr,)
    if isinstance(node, Binary):
        return (node.left, node.right)
    if isinstance(node, Function):
        return node.args
    if isinstance(node, Aggregate):
        return (node.expr, *node.params)
    return ()


def walk(node: Node) -> Iterator[Node]:
    """Yield every node in pre-order."""
    yield node
    for child in children(node):
        yield from walk(child)


def tree_depth(node: Node) -> int:
    """Return maximum nesting depth (a leaf has depth one)."""
    nested = children(node)
    return 1 + max((tree_depth(child) for child in nested), default=0)


def selector_matchers(selector: VectorSelector) -> tuple[Matcher, ...]:
    """Return selector matchers including an implicit metric-name matcher."""
    if not selector.metric:
        return selector.matchers
    return (Matcher("__name__", "=", selector.metric), *selector.matchers)
