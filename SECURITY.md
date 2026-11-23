# Security Policy — baysoz-queryoracle

Baysöz Labs <baysozlabs@proton.me>

## Supported versions

| Version | Supported |
| --- | --- |
| 1.0.x | Yes |

## Threat model

The tool processes untrusted PromQL text, fixture JSON and query-pack JSON in CI. It must not execute queries against production systems, must not open network connections, and must not interpret fixture content as code.

Controls implemented:

- **No dynamic execution**: `eval`, `exec`, shell invocation and imports from user data are absent. Label matchers use Python `re` only after strict string validation; malformed regexes raise `EvaluationError`.
- **Strict schema validation**: fixtures and packs require exact schema identifiers, finite numeric samples, unique timestamps and unique label sets. Path traversal is not possible because the pack's fixture path is resolved relative to the pack file and read with ordinary file I/O; no globbing or remote URL is accepted.
- **Bounded analysis**: parenthesis nesting, AST depth, fan-out score and rule findings are reported rather than executed. Oversized expressions are diagnosed, not expanded.
- **Deterministic output**: SARIF contains no absolute paths or timestamps, preventing accidental leakage of CI host layout into published artifacts.
- **Dependency surface**: zero runtime dependencies; the supply chain is limited to Python 3.13+, setuptools at build time, and the developer tools declared in CI.

## Reporting a vulnerability

Email **baysozlabs@proton.me** with a description, reproduction expression or fixture, affected version and impact assessment. Please do not open a public issue for an unpatched vulnerability. We target an initial response within three business days and a coordinated disclosure after a fix is released.

## CI enforcement

Every pull request runs the full test suite, `ruff --select E,F,W,S,B` and `semgrep --config p/default --error`. A release is blocked when any gate reports a finding.

Attribution: Baysöz Labs <baysozlabs@proton.me>
