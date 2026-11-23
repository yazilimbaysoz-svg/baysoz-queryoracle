# Architecture — baysoz-queryoracle

Baysöz Labs <baysozlabs@proton.me>

## System boundary

```
 PromQL text ──> lexer ──> parser ──> immutable AST
                              │
                ┌─────────────┼──────────────────┐
                v             v                  v
          rules/profile   evaluator          SARIF writer
           analysis       + fixtures          (deterministic)
                │             │                  │
                └───── check / eval / verify CLI ─┘
```

The process is pure: no network calls, no daemon, no ambient clock, no host-specific paths in output. Identical input bytes produce identical bytes on stdout and identical finding fingerprints.

## Layers

| Module | Responsibility | Key invariant |
| --- | --- | --- |
| `lexer.py` | Positioned tokens, duration literals, strings, comments | Every token carries an absolute offset; invalid characters fail with an offset. |
| `ast.py` | Frozen dataclasses for selectors, operators, functions, aggregations, subqueries | AST is immutable and hashable except label dictionaries; `walk` is pre-order and stable. |
| `parser.py` | Precedence climbing, matchers, vector matching modifiers, grouping before/after aggregation | Parsing never executes code and never consults a backend. |
| `profiles.py` | Conservative per-backend feature allowlists | A feature is only "verified" for a profile when explicitly listed; unknown means unverified. |
| `rules.py` | 16 diagnostic rules plus a transparent fan-out score | Each finding has a stable rule id, severity, expression, profile and remediation. |
| `fixtures.py` | Strict validation of `fixture-v1` and `pack-v1` JSON | Duplicate timestamps, duplicate label sets and non-finite values are rejected. |
| `evaluate.py` | Instant-vector evaluation, counter math, vector matching, classic histogram quantile | Only documented functions execute; everything else raises an explicit `EvaluationError`. |
| `sarif.py` | SARIF 2.1.0 serialization | No timestamps, no absolute paths; fingerprints are SHA-256 prefixes of rule + expression + message. |
| `cli.py` | Five subcommands and exit-code gate | Exit 0 pass, 1 gate failure, 2 input/schema error; attribution appears in version and every help screen. |

## Semantic coverage (explicit, no stubs)

- **Selectors**: metric names, `{label="v"}`, `!=`, `=~`, `!~`, `[range]`, `offset`, `@ timestamp`, `@ start()`/`@ end()`.
- **Operators**: `+ - * / % ^`, comparisons, `bool`, `and/or/unless`, `on()`, `ignoring()`, `group_left()`, `group_right()`.
- **Aggregations**: `sum min max avg count group stddev stdvar topk bottomk quantile count_values`.
- **Functions**: counter/reset math (`rate`, `irate`, `increase`, `delta`, `idelta`, `resets`), `*_over_time`, `histogram_quantile`, scalar math, `clamp*`, `scalar`, `time`.
- **Not executed offline**: subqueries, native histograms, window-dependent modifiers, backend extensions such as `limitk` and `label_replace`. These are *detected and reported* by rules `QO008`/`QO012`, never silently approximated.

## Cost model

The fan-out score is a documented heuristic, not a measurement of a live TSDB:

| Shape | Added score |
| --- | --- |
| selector with no metric name and no `__name__` matcher | +60 |
| unanchored `=~` or `!~` matcher | +25 |
| unanchored regex on a high-cardinality label (`pod`, `instance`, `uid`, …) | +20 additional |
| equality match on a high-cardinality label | +2 |
| each disjunction branch beyond three | +10 |

Tiers: `low` < 20, `moderate` < 50, `elevated` < 100, `severe` >= 100. The score is printed with every check so reviewers can see why a gate fired.

## Determinism

- Sample selection uses the half-open range `(t - window, t]`, matching Prometheus range-vector semantics.
- Counter resets add the pre-reset value to the delta; `rate` divides by the observed timestamp span, never by configuration.
- Classic `histogram_quantile` aggregates buckets by `le`, enforces monotonic cumulative counts, interpolates linearly inside the target bucket and ignores `+Inf` as a cumulative anchor.
- Output vectors are sorted by canonical label signature; numbers use six fixed decimals with trailing zeros removed.
- SARIF fingerprints are content hashes, so a re-run of an unchanged query produces an unchanged report.

## Extension points

New backend profiles are entries in `profiles.py`. New rules are rule-catalog entries plus a walk in `rules.py`. New evaluator functions require an implementation in `_function` and membership in `OFFLINE_FUNCTIONS`; both must be covered by tests in `tests/`.

Attribution: Baysöz Labs <baysozlabs@proton.me>
