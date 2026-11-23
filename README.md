# baysoz-queryoracle

[![CI](https://github.com/yazilimbaysoz-svg/baysoz-queryoracle/actions/workflows/ci.yml/badge.svg)](https://github.com/yazilimbaysoz-svg/baysoz-queryoracle/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.13%2B-blue)
![License](https://img.shields.io/badge/license-MIT-green)
![Output](https://img.shields.io/badge/output-SARIF%202.1.0-orange)
![Attribution](https://img.shields.io/badge/by-Bays%C3%B6z%20Labs-baysozlabs%40proton.me)

**Deterministic offline PromQL conformance oracle.** Parse, lint, cost-check and evaluate PromQL against synthetic fixtures, verify cross-backend portability, and gate CI with SARIF — with no services, no network and no local LLM.

By **Baysöz Labs <baysozlabs@proton.me>**

## Why

PromQL now runs on Prometheus, Thanos, Mimir, VictoriaMetrics, ClickHouse gateways, GreptimeDB and Elasticsearch. Query shapes that are valid on one backend can be rejected or silently diverge on another. Existing portfolio tools cover Collector configs, cardinality emission and SLO burn policies — none verify the query text itself. See [docs/MARKET_RESEARCH.md](docs/MARKET_RESEARCH.md) for the live 2026 ecosystem evidence.

## Install

```bash
uv venv
uv pip install -e .
# or: uv tool install git+https://github.com/yazilimbaysoz-svg/baysoz-queryoracle.git
```

Requires Python 3.13+. Runtime dependencies: **none**.

## Architecture

```
 PromQL ──> lexer ──> parser ──> immutable AST
                          ├── rules + backend profiles ──> text / JSON / SARIF
                          └── fixture + instant evaluator ──> golden verification
```

Modules: `lexer.py`, `ast.py`, `parser.py`, `profiles.py`, `rules.py`, `fixtures.py`, `evaluate.py`, `sarif.py`, `cli.py`. Full semantics and invariants: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Real CLI output

```console
$ baysoz-queryoracle --version
baysoz-queryoracle 1.0.0 — Baysöz Labs <baysozlabs@proton.me>

$ baysoz-queryoracle check 'sum by (job) (rate(http_requests_total[30s]))' 'sum(rate(http_errors_total[45s])) / sum(rate(http_requests_total[45s]))'
baysoz-queryoracle 1.0.0 — Baysöz Labs <baysozlabs@proton.me>
profile: prometheus_3
query: sum by (job) (rate(http_requests_total[30s]))
  features: core PromQL
  fan-out: 0 (low)
query: sum(rate(http_errors_total[45s])) / sum(rate(http_requests_total[45s]))
  features: core PromQL
  fan-out: 0 (low)
no findings
summary: 0 findings (0 error, 0 warning) | cost 0/low
gate: PASS (fail-on warning)

$ baysoz-queryoracle check 'count({__name__=~".+"}) or {pod=~".*"}' --profile prometheus_3 --target clickhouse --target elasticsearch
baysoz-queryoracle 1.0.0 — Baysöz Labs <baysozlabs@proton.me>
profile: prometheus_3 targets: clickhouse,elasticsearch
query: count({__name__=~".+"}) or {pod=~".*"}
  features: core PromQL
  fan-out: 130 (severe)
QO002 warning @0    estimated fan-out score 130 (severe) from matchers and disjunctions
    profile: prometheus_3
    fix: narrow selectors with equality matchers
QO002 warning @0    series selector has no metric name or __name__ matcher; scans the whole index
    profile: prometheus_3
    fix: add a metric name or an anchored __name__ matcher
QO003 warning @0    unanchored regex '.*' on 'pod' forces a full label scan
    profile: prometheus_3
    fix: anchor the regex with ^...$
QO003 warning @0    unanchored regex '.+' on '__name__' forces a full label scan
    profile: prometheus_3
    fix: anchor the regex with ^...$
QO004 warning @0    high-cardinality label 'pod' is matched by an unanchored regex
    profile: prometheus_3
    fix: prefer an equality matcher or an anchored, bounded regex
summary: 5 findings (0 error, 5 warning) | cost 130/severe
gate: FAIL (fail-on warning)

$ baysoz-queryoracle eval 'sum by (job) (rate(http_requests_total[30s]))' --fixture examples/fixture.json --at 45
baysoz-queryoracle 1.0.0 — Baysöz Labs <baysozlabs@proton.me>
at: 45s  resultType: vector
  {job="checkout"} => 1.6

$ baysoz-queryoracle verify examples/pack.json
baysoz-queryoracle 1.0.0 — Baysöz Labs <baysozlabs@proton.me>
pack queries: 3
  checkout request rate: PASS
  p95 checkout latency: PASS
  checkout error ratio: PASS
no findings
summary: 0 findings (0 error, 0 warning)
gate: PASS (fail-on warning)
```

Every subcommand also supports `--format json`; `check` and `verify` support `--format sarif`. Exit codes: `0` pass, `1` gate failure, `2` invalid input/schema.

## CI usage

```yaml
- name: Gate PromQL queries
  run: |
    baysoz-queryoracle check "$(cat alerts/error-budget.promql)" \
      --profile prometheus_3 --target mimir \
      --format sarif > promql.sarif
- name: Upload SARIF
  uses: github/codeql-action/upload-sarif@d2aacf54c2fbc910db8d7ec338fdb54ea48c692d # v3
  with:
    sarif_file: promql.sarif
```

## Library usage

```python
from baysoz_queryoracle.rules import analyze

result = analyze('rate(http_requests_total{job="api"}[5m])', profile="prometheus_3")
assert result.findings == ()
```

## Rules and profiles

`baysoz-queryoracle rules` prints the catalog; `baysoz-queryoracle profiles` prints all verified backend feature sets. Highlights: `QO001` syntax, `QO002`–`QO004` selector cost, `QO005` short rate windows, `QO006` histogram buckets, `QO007` nesting, `QO008` backend portability, `QO010` division by zero, `QO011`/`QO012` function coverage, `QO030` golden mismatch.

Profiles: `prometheus_2`, `prometheus_3`, `thanos`, `mimir`, `victoriametrics`, `clickhouse`, `elasticsearch`, `grafana_cloud_monitoring`. A feature not explicitly verified for a profile is reported as `QO008` — conservative by design.

## Limits

Subqueries, native histograms and backend-specific extensions are **detected but not executed**; the evaluator raises an explicit error rather than guessing. Unsupported functions are surfaced as `QO012`. The cost score is a published heuristic, not a live TSDB measurement.

## Development

```bash
uv venv && uv pip install -e . && uv pip install pytest
uv run pytest -q
ruff check --select E,F,W,S,B .
semgrep --config p/default --error .
```

Security reports: **baysozlabs@proton.me**. See [SECURITY.md](SECURITY.md), [PLAN.md](PLAN.md) and [CHANGELOG.md](CHANGELOG.md).

Attribution: Baysöz Labs <baysozlabs@proton.me>
