"""Deterministic fixture and query-pack loading — Baysöz Labs <baysozlabs@proton.me>."""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from pathlib import Path

from .errors import FixtureError, ParseError
from .lexer import duration_to_seconds

FIXTURE_SCHEMA = "baysoz-queryoracle/fixture-v1"
PACK_SCHEMA = "baysoz-queryoracle/pack-v1"
_LABEL_NAME = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")


@dataclass(frozen=True, slots=True)
class Sample:
    """A timestamped numeric sample."""

    timestamp: float
    value: float


@dataclass(frozen=True, slots=True)
class Series:
    """One labeled time series."""

    labels: dict[str, str]
    samples: tuple[Sample, ...]


@dataclass(frozen=True, slots=True)
class Fixture:
    """An immutable synthetic series fixture."""

    series: tuple[Series, ...]


@dataclass(frozen=True, slots=True)
class PackQuery:
    """One query plus its golden expectation inside a query pack."""

    name: str
    expression: str
    at: float
    expect: dict[str, object]


@dataclass(frozen=True, slots=True)
class QueryPack:
    """A portable, offline-conformance query pack."""

    fixture: Fixture
    scrape_interval_s: float
    queries: tuple[PackQuery, ...]


def _parse_interval(value: object) -> float:
    if isinstance(value, str):
        try:
            return duration_to_seconds(value)
        except ParseError as error:
            raise FixtureError(f"invalid scrape interval: {value}") from error
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
        return float(value)
    raise FixtureError("scrape interval must be a duration string or positive number")


def _validate_labels(labels: object, context: str) -> dict[str, str]:
    if not isinstance(labels, dict):
        raise FixtureError(f"{context}: labels must be an object")
    result: dict[str, str] = {}
    for key, value in labels.items():
        if not isinstance(key, str) or not _LABEL_NAME.fullmatch(key):
            raise FixtureError(f"{context}: invalid label name {key!r}")
        if not isinstance(value, str):
            raise FixtureError(f"{context}: label {key!r} must be a string")
        result[key] = value
    return result


def load_fixture(path: str | Path) -> Fixture:
    """Load and validate a fixture file."""
    return fixture_from_dict(_read_json(path))


def fixture_from_dict(document: object) -> Fixture:
    """Validate a fixture JSON document."""
    if not isinstance(document, dict) or document.get("schema") != FIXTURE_SCHEMA:
        raise FixtureError(f"fixture schema must be exactly {FIXTURE_SCHEMA!r}")
    raw_series = document.get("series")
    if not isinstance(raw_series, list) or not raw_series:
        raise FixtureError("fixture 'series' must be a non-empty array")
    seen: set[tuple[tuple[str, str], ...]] = set()
    result: list[Series] = []
    for index, entry in enumerate(raw_series):
        context = f"series[{index}]"
        if not isinstance(entry, dict):
            raise FixtureError(f"{context} must be an object")
        labels = _validate_labels(entry.get("labels"), context)
        raw_samples = entry.get("samples")
        if not isinstance(raw_samples, list) or not raw_samples:
            raise FixtureError(f"{context}: samples must be a non-empty array")
        samples: list[Sample] = []
        for sample_index, item in enumerate(raw_samples):
            if not isinstance(item, list) or len(item) != 2:
                raise FixtureError(f"{context}.samples[{sample_index}] must be [timestamp, value]")
            timestamp, value = item
            if isinstance(timestamp, bool) or not isinstance(timestamp, (int, float)):
                raise FixtureError(f"{context}.samples[{sample_index}] has an invalid timestamp")
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
            ):
                raise FixtureError(f"{context}.samples[{sample_index}] has a non-finite value")
            samples.append(Sample(float(timestamp), float(value)))
        samples.sort(key=lambda sample: sample.timestamp)
        timestamps = [sample.timestamp for sample in samples]
        if len(set(timestamps)) != len(timestamps):
            raise FixtureError(f"{context}: duplicate sample timestamps")
        signature = tuple(sorted(labels.items()))
        if signature in seen:
            raise FixtureError(f"{context}: duplicate label set")
        seen.add(signature)
        result.append(Series(labels, tuple(samples)))
    return Fixture(tuple(result))


def load_pack(path: str | Path) -> QueryPack:
    """Load a query pack and its referenced fixture relative to the pack file."""
    pack_path = Path(path)
    document = _read_json(pack_path)
    if not isinstance(document, dict) or document.get("schema") != PACK_SCHEMA:
        raise FixtureError(f"pack schema must be exactly {PACK_SCHEMA!r}")
    scrape_interval_s = _parse_interval(document.get("scrape_interval", "15s"))
    fixture_reference = document.get("fixture")
    if not isinstance(fixture_reference, str):
        raise FixtureError("pack 'fixture' must be a path string")
    fixture_path = pack_path.parent / fixture_reference
    fixture = load_fixture(fixture_path)
    raw_queries = document.get("queries")
    if not isinstance(raw_queries, list) or not raw_queries:
        raise FixtureError("pack 'queries' must be a non-empty array")
    queries: list[PackQuery] = []
    for index, entry in enumerate(raw_queries):
        context = f"queries[{index}]"
        if not isinstance(entry, dict):
            raise FixtureError(f"{context} must be an object")
        name, expression = entry.get("name"), entry.get("expr")
        if not isinstance(name, str) or not name:
            raise FixtureError(f"{context}: name must be a non-empty string")
        if not isinstance(expression, str) or not expression:
            raise FixtureError(f"{context}: expr must be a non-empty string")
        at = entry.get("at")
        if isinstance(at, bool) or not isinstance(at, (int, float)) or not math.isfinite(at):
            raise FixtureError(f"{context}: at must be a finite numeric timestamp")
        expect = entry.get("expect")
        if not isinstance(expect, dict) or expect.get("resultType") not in {"vector", "scalar"}:
            raise FixtureError(f"{context}: expect must declare resultType vector or scalar")
        queries.append(PackQuery(name, expression, float(at), expect))
    return QueryPack(fixture, scrape_interval_s, tuple(queries))


def _read_json(path: str | Path) -> object:
    source = Path(path)
    try:
        return json.loads(source.read_text(encoding="utf-8"))
    except OSError as error:
        raise FixtureError(f"cannot read {source}: {error}") from error
    except json.JSONDecodeError as error:
        raise FixtureError(f"invalid JSON in {source}: {error.msg}") from error
