"""Conservative backend portability profiles — Baysöz Labs <baysozlabs@proton.me>."""

from __future__ import annotations

from .ast import Aggregate, Function, Node, Subquery, VectorSelector, walk

FEATURES: dict[str, str] = {
    "at_modifier": "@ timestamp modifier",
    "subquery": "subquery expression [range:step]",
    "negative_offset": "negative offset (MetricsQL extension)",
    "native_histograms": "native histogram functions",
    "limitk": "limitk aggregation",
    "limit_ratio": "limit_ratio aggregation",
    "mad_over_time": "mad_over_time function",
    "atan2": "atan2 function",
    "info_function": "info function",
    "group_aggregation": "group aggregation operator",
    "count_values_aggregation": "count_values aggregation operator",
    "resets_function": "resets function",
    "absent_over_time": "absent_over_time function",
    "sort_by_label": "sort_by_label function",
}

_CORE = frozenset({"histogram_quantile", "bool_modifier", "group_left"})

_PROMETHEUS_3 = _CORE | {
    "at_modifier",
    "subquery",
    "native_histograms",
    "limitk",
    "limit_ratio",
    "mad_over_time",
    "atan2",
    "info_function",
    "group_aggregation",
    "count_values_aggregation",
    "resets_function",
    "absent_over_time",
    "sort_by_label",
}

_PROMETHEUS_2 = _CORE | {
    "at_modifier",
    "subquery",
    "native_histograms",
    "group_aggregation",
    "count_values_aggregation",
    "resets_function",
    "absent_over_time",
    "sort_by_label",
}

PROFILES: dict[str, frozenset[str]] = {
    "prometheus_3": frozenset(_PROMETHEUS_3),
    "prometheus_2": frozenset(_PROMETHEUS_2),
    "thanos": frozenset(_PROMETHEUS_2 | {"native_histograms"}),
    "mimir": frozenset(_PROMETHEUS_2 | {"native_histograms"}),
    "victoriametrics": frozenset(
        _CORE
        | {
            "at_modifier",
            "subquery",
            "negative_offset",
            "limitk",
            "limit_ratio",
            "count_values_aggregation",
            "absent_over_time",
            "sort_by_label",
        }
    ),
    "clickhouse": frozenset(_CORE),
    "elasticsearch": frozenset(_CORE | {"at_modifier", "subquery", "native_histograms"}),
    "grafana_cloud_monitoring": frozenset(_CORE),
}

_NATIVE_HISTOGRAM_PREFIXES = ("double_exponential_", "histogram_fraction", "histogram_std")


def detect_features(node: Node) -> frozenset[str]:
    """Detect portability-sensitive features in an AST."""
    found: set[str] = set()
    for item in walk(node):
        if isinstance(item, (VectorSelector, Subquery)):
            if item.at is not None:
                found.add("at_modifier")
            if item.offset_s is not None and item.offset_s < 0:
                found.add("negative_offset")
        if isinstance(item, Function):
            name = item.name
            if name.startswith(_NATIVE_HISTOGRAM_PREFIXES):
                found.add("native_histograms")
            elif name in {"limitk", "limit_ratio", "mad_over_time", "atan2", "info", "resets"}:
                found.add(name if name != "info" else "info_function")
            elif name in {"absent_over_time", "sort_by_label"}:
                found.add(name)
        if isinstance(item, Aggregate) and item.operator in {"limitk", "limit_ratio"}:
            found.add(item.operator)
        if isinstance(item, Aggregate) and item.operator in {"group", "count_values"}:
            found.add(f"{item.operator}_aggregation")
    if any(isinstance(item, Subquery) for item in walk(node)):
        found.add("subquery")
    return frozenset(found)


def unsupported_features(features: frozenset[str], profile: str) -> tuple[str, ...]:
    """Return features outside a profile's conservative verified subset."""
    if profile not in PROFILES:
        raise KeyError(profile)
    return tuple(sorted(feature for feature in features if feature not in PROFILES[profile]))


def profile_names() -> tuple[str, ...]:
    """Return all profile names in stable order."""
    return tuple(PROFILES)
