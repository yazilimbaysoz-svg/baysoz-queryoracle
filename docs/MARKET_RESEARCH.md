# Market Research — Offline PromQL Conformance for Observability

Baysöz Labs <baysozlabs@proton.me> · research snapshot: 2026-11-23 · all signals gathered with `gh search repos` and `gh search issues`.

## 1. Ecosystem signals (live GitHub mining)

| Signal | Evidence | What it means |
| --- | --- | --- |
| Prometheus remains the query lingua franca | `prometheus/prometheus` 67.7k stars; `grafana/grafana` 78.0k; `VictoriaMetrics/VictoriaMetrics` 18.2k; `grafana/mimir` 5.3k | PromQL query packs are long-lived production assets, not throwaway scripts. |
| Alternative execution backends multiply | `GreptimeTeam/greptimedb` 6.8k; `parseablehq/parseable` 2.6k; `tsouza/cerberus` 73 stars (PromQL→ClickHouse gateway); `elastic/elasticsearch` PromQL issues; `PostHog/snuffle` | The same query no longer means the same thing on every backend. Divergence is an integration defect, not a style preference. |
| Real divergence bugs are open now | `PostHog/snuffle`: "promql: instant selectors keep stale series, reading 3-5% above VictoriaMetrics" (2026-11); `tsouza/cerberus`: "promql: query shapes refused with 422 where Prometheus answers an empty result" and "query shapes that fail in ClickHouse with 502 where Prometheus answers" (2026-11-16); `grafana/grafana-cloudmonitoring-datasource`: "PromQL query type panics the alerting scheduler (nil deref at promql_query.go:23)" (2026-08); `ArcadeData/arcadedb`: PromQL endpoint returns 500 for a matcher ending at its operator (2026-11-15) | Backend-specific acceptance and semantics drift silently. Teams need a pre-CI oracle that says *which backend verified this shape* before a dashboard or alert ships. |
| Query-language coverage is still incomplete | `elastic/roadmap`: "Extend PromQL language coverage to 92%" (2026); open issues in `elastic/elasticsearch` for `info`, `atan2`, `group`, `resets`, `abs` | Feature support is uneven and moving. A conservative allowlist per backend is more useful than assuming universal PromQL. |
| Synthetic-fixture validation is a repeated, unresolved need | `kubevirt/ci-health`: "test(cost): validate PromQL queries without a live cluster" (2026-11-10); `SynapsVault/deploy`: "Validate Grafana PromQL queries against metric fixtures" (2026-11-17); `thillainirmal-tech/CloudPulse-AI`: "Add PromQL query-pack validation with synthetic fixtures" (2026-10-29); `pgarciaq/ros-ocp-backend`: "Test harness: evaluate a PromQL query string against a fixture (behavioural gate coverage)" (2026-11-18) | Multiple independent teams are rebuilding the same offline harness. A portable pack format plus deterministic evaluator removes duplicated toil. |
| Query cost and parser safety are first-class concerns | `prometheus/prometheus`: "promql/parser: bound expression nesting during semantic validation" (2026); `GreptimeTeam/greptimedb`: "High memory usage for long-range PromQL queries" (2026-10); `nicolastakashi/prom-analytics-proxy` (159 stars) captures query analytics in production | Linting unanchored regexes, unbounded selectors and unsafe nesting before deployment prevents expensive queries from reaching the TSDB. |
| Adjacent tools solve other layers | `prometheus/promlens` (677 stars) is an interactive builder/explainer; `VictoriaMetrics/metricsql` (250 stars) and `GreptimeTeam/promql-parser` (105 stars) are parsers; `prometheus-community/promql-langserver` (188 stars) is editor support | No tool in the mined set combines *CI gate + fixture evaluation + cross-backend profile + SARIF* in a single offline binary. |

## 2. Non-redundancy against the Baysöz Labs portfolio

The 21 repositories in `existing-repos.json` were checked individually. Adjacent work and the deliberate boundary:

- `baysoz-otelgate` lints **Collector YAML configuration**; this project analyzes **PromQL query text** and its runtime fan-out.
- `baysoz-cardinalis` estimates **cardinality of emitted telemetry** with HLL++; this project estimates **query-side index fan-out** from selector shape and detects matcher anti-patterns.
- `baysoz-burnlattice` compiles **SLO burn-rate policies**; this project verifies the **queries behind dashboards and alerts**, including histogram bucket arithmetic.
- `baysoz-logmask`, `baysoz-desyncforge`, `bolaforge`, `baysoz-josegate`, `planradius`, `baysoz-netpol-oracle`, `baysoz-routegate`, `baysoz-actionlock`, `atr-parity-gate`, `envpair`, `envlens` and the other listed tools operate in entirely different domains (log redaction, HTTP parser differential, authz, JOSE, IaC, networking, supply chain, detection rules, config drift).
- `redos-lint` is a regex DoS linter; this project may *flag* unanchored regex matchers, but it does so as one of sixteen observability-specific rules, with no regex-engine analysis.
- `yazilimbaysoz-svg`, `yazilimbaysoz-svg.github.io` and the ERP repo are showcase/site/business applications, not observability gates.

Conclusion: no overlap, no toy utility, no rebranding of an existing repository.

## 3. Why this is needed in 2026-2027

1. **PromQL is now a portability surface.** Grafana, Thanos, Mimir, VictoriaMetrics, ClickHouse gateways, GreptimeDB and Elasticsearch all accept PromQL-shaped input with different feature sets and edge semantics. Query text must be checked against an explicit target profile.
2. **CI cannot assume a live TSDB.** Clusters are expensive, ephemeral and often air-gapped. The repeated issues above show teams wants fixture-driven query tests without one.
3. **Regression reporting must be machine-consumable.** SARIF is the standard CI interchange; a query gate that only prints prose cannot be enforced uniformly across GitHub Actions, GitLab and enterprise dashboards.
4. **Cost is a correctness property.** An unanchored `pod=~".*"` matcher can turn a cheap dashboard into a full-index scan. This must be detected at review time, not during an incident.

## 4. Product decision

Build **baysoz-queryoracle**: a dependency-free Python 3.13+ CLI/library with a deterministic PromQL lexer/parser, transparent fan-out scoring, conservative backend profiles, an in-memory instant evaluator for classic histograms and counter arithmetic, golden query packs, and SARIF 2.1.0 output. Zero services, zero local LLMs, zero heavyweight runtimes. This is the missing verification layer between query authoring and a multi-backend production fleet.

Attribution: Baysöz Labs <baysozlabs@proton.me>
