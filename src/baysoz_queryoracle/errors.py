"""Typed errors for the Baysöz Labs <baysozlabs@proton.me> query oracle."""

from __future__ import annotations


class QueryOracleError(Exception):
    """Base class for deterministic oracle failures."""


class ParseError(QueryOracleError):
    """Raised when a PromQL expression cannot be parsed."""

    def __init__(self, message: str, position: int = 0) -> None:
        super().__init__(f"{message} at offset {position}")
        self.message = message
        self.position = position


class FixtureError(QueryOracleError):
    """Raised when a fixture or query pack violates its schema."""


class EvaluationError(QueryOracleError):
    """Raised when an expression cannot be evaluated against a fixture."""
