# Changelog — baysoz-queryoracle

Baysöz Labs <baysozlabs@proton.me>

All notable changes to this project are documented here. The format follows Keep a Changelog and Semantic Versioning.

## [1.0.0] — 2026-11-23

### Added

- Deterministic PromQL lexer, precedence-climbing parser and immutable AST with offsets.
- Sixteen stable diagnostic rules (`QO001`–`QO030`) covering syntax, fan-out, cardinality, short rate windows, histogram buckets, nesting, portability and divisions by zero.
- Eight conservative backend profiles: `prometheus_2`, `prometheus_3`, `thanos`, `mimir`, `victoriametrics`, `clickhouse`, `elasticsearch`, `grafana_cloud_monitoring`.
- Strict `fixture-v1` and `pack-v1` validators with golden-result comparison.
- Offline instant evaluator for selectors, binary vector matching, aggregations, counter/reset functions, `*_over_time` and classic `histogram_quantile`.
- SARIF 2.1.0 output with content-hash fingerprints and attribution metadata.
- CLI subcommands `check`, `eval`, `verify`, `profiles`, `rules`; all help and version output attributed to Baysöz Labs <baysozlabs@proton.me>.
- Example fixture and query pack, CI workflow, security policy, architecture and market research.

### Changed

- Initial public release; no prior versions.

### Fixed

- Not applicable.

Attribution: Baysöz Labs <baysozlabs@proton.me>
