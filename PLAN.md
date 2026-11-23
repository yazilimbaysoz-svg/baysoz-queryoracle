# Plan — baysoz-queryoracle

Baysöz Labs <baysozlabs@proton.me>

## Delivered

1. Market mining with `gh search repos` / `gh search issues`; portfolio de-duplication against all 21 entries of `existing-repos.json`.
2. Enterprise repository layout: `docs/`, `src/baysoz_queryoracle/`, `tests/`, `examples/`, `.github/workflows/ci.yml`, `pyproject.toml`, `LICENSE`, `README.md`, `SECURITY.md`, `CHANGELOG.md`, `PLAN.md`.
3. Dependency-free Python 3.13+ package with a real PromQL lexer, parser, immutable AST, rule engine, backend profiles, fixture validator, instant evaluator and SARIF writer.
4. Five CLI subcommands: `check`, `eval`, `verify`, `profiles`, `rules` — each help screen and `--version` attributes Baysöz Labs <baysozlabs@proton.me>.
5. Query packs and fixtures under `examples/` with golden results for counter rate, histogram quantile and error ratio.
6. Quality gates: `uv venv`, `uv pip install -e .`, `uv pip install pytest`, `uv run pytest -q`, `ruff check --select E,F,W,S,B .`, `semgrep --config p/default --error .`.
7. Supreme Grand Jury Tribunal: live adversarial execution with syntax bombs, cardinality bombs, cross-backend feature probes, negative fixture cases and golden-result tampering. Unanimous pass required for release.
8. Public release as `yazilimbaysoz-svg/baysoz-queryoracle` with topics and factory result metadata.

## Non-goals

- No Prometheus server, Grafana, VictoriaMetrics, ClickHouse or other service is started by this tool.
- No local LLM, embedding model or network inference is used.
- No telemetry is uploaded; the tool never phones home.
- No backend is emulated beyond the documented deterministic subset; unsupported features are reported explicitly.

## Future work (post-release)

- Additional profiles contributed by backend maintainers, with explicit feature ratification.
- Recording-rule DAG analysis that propagates golden expectations across derived metrics.
- Optional JSON Schema publication for `pack-v1` to enable editor validation.

Attribution: Baysöz Labs <baysozlabs@proton.me>
