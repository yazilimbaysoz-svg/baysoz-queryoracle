"""Command-line interface — Baysöz Labs <baysozlabs@proton.me>."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence

from . import ATTRIBUTION, __version__
from .errors import QueryOracleError
from .evaluate import Evaluator, format_value
from .fixtures import load_fixture, load_pack
from .parser import parse
from .profiles import FEATURES, PROFILES, profile_names
from .rules import RULE_CATALOG, SEVERITY_ORDER, Analysis, Finding, analyze
from .sarif import build_sarif

_BANNER = f"baysoz-queryoracle {__version__} — {ATTRIBUTION}"
_EPILOG = f"Deterministic offline PromQL conformance oracle. {ATTRIBUTION}"


def _duration(value: str) -> float:
    from .lexer import duration_to_seconds

    return duration_to_seconds(value)


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--format", choices=("text", "json", "sarif"), default="text")
    parser.add_argument("--fail-on", choices=("error", "warning", "none"), default="warning")


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI parser with attribution on every help screen."""
    parser = argparse.ArgumentParser(
        prog="baysoz-queryoracle",
        description=f"Offline PromQL conformance, cost and portability gate. {ATTRIBUTION}",
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=_BANNER)
    subparsers = parser.add_subparsers(dest="command", required=True)

    check = subparsers.add_parser(
        "check",
        help="lint expressions and emit a CI gate",
        description=f"Analyze PromQL expressions offline. {ATTRIBUTION}",
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    check.add_argument("expressions", nargs="+", help="PromQL expressions to analyze")
    check.add_argument("--profile", default="prometheus_3", choices=profile_names())
    check.add_argument("--target", action="append", default=[], choices=profile_names())
    check.add_argument("--scrape-interval", default="15s")
    check.add_argument("--max-depth", type=int, default=12)
    _add_common(check)

    evaluate = subparsers.add_parser(
        "eval",
        help="evaluate one expression against a fixture",
        description=f"Evaluate PromQL against synthetic series. {ATTRIBUTION}",
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    evaluate.add_argument("expression", help="PromQL expression to evaluate")
    evaluate.add_argument("--fixture", required=True, help="path to fixture-v1 JSON")
    evaluate.add_argument("--at", type=float, default=0.0, help="evaluation timestamp in seconds")
    evaluate.add_argument("--lookback", type=float, default=300.0)
    evaluate.add_argument("--scrape-interval", default="15s")
    evaluate.add_argument("--format", choices=("text", "json"), default="text")

    verify = subparsers.add_parser(
        "verify",
        help="verify a query pack against golden results",
        description=f"Run deterministic PromQL query-pack conformance. {ATTRIBUTION}",
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    verify.add_argument("pack", help="path to pack-v1 JSON")
    verify.add_argument("--scrape-interval", default="15s")
    _add_common(verify)

    profiles = subparsers.add_parser(
        "profiles",
        help="list backend portability profiles",
        description=f"Backend feature profiles. {ATTRIBUTION}",
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    profiles.add_argument("--format", choices=("text", "json"), default="text")

    rules = subparsers.add_parser(
        "rules",
        help="list diagnostic rules",
        description=f"Diagnostic rule catalog. {ATTRIBUTION}",
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    rules.add_argument("--format", choices=("text", "json"), default="text")
    return parser


def _gate(findings: tuple[Finding, ...], fail_on: str) -> bool:
    if fail_on == "none" or not findings:
        return False
    threshold = SEVERITY_ORDER[fail_on]
    return any(SEVERITY_ORDER[finding.severity] >= threshold for finding in findings)


def _render_findings(findings: tuple[Finding, ...]) -> list[str]:
    lines: list[str] = []
    for finding in findings:
        lines.append(
            f"{finding.rule_id} {finding.severity:<7} @{finding.position:<4} {finding.message}"
        )
        if finding.profile:
            lines.append(f"    profile: {finding.profile}")
        if finding.remediation:
            lines.append(f"    fix: {finding.remediation}")
    return lines


def _summary(findings: tuple[Finding, ...], analysis: Analysis | None = None) -> str:
    errors = sum(finding.severity == "error" for finding in findings)
    warnings = sum(finding.severity == "warning" for finding in findings)
    cost = f" | cost {analysis.cost_score}/{analysis.cost_tier}" if analysis else ""
    return f"summary: {len(findings)} findings ({errors} error, {warnings} warning){cost}"


def _run_check(args: argparse.Namespace) -> int:
    interval = _duration(args.scrape_interval)
    analyses = [
        analyze(expression, args.profile, interval, args.max_depth)
        for expression in args.expressions
    ]
    findings = tuple(finding for analysis in analyses for finding in analysis.findings)
    for target in dict.fromkeys(args.target):
        if target == args.profile:
            continue
        target_findings = analyze(args.expressions[0], target, interval, args.max_depth).findings
        findings += tuple(
            Finding(
                finding.rule_id,
                f"{finding.message} (target {target})",
                finding.position,
                finding.expression,
                finding.profile,
                finding.remediation,
            )
            for finding in target_findings
            if finding.rule_id == "QO008"
        )
    failed = _gate(findings, args.fail_on)
    if args.format == "sarif":
        print(json.dumps(build_sarif(findings, __version__), indent=2, sort_keys=True))
    elif args.format == "json":
        print(
            json.dumps(
                {
                    "attribution": ATTRIBUTION,
                    "version": __version__,
                    "profile": args.profile,
                    "targets": list(dict.fromkeys(args.target)),
                    "analyses": [
                        {
                            "expression": analysis.expression,
                            "features": list(analysis.features),
                            "cost_score": analysis.cost_score,
                            "cost_tier": analysis.cost_tier,
                        }
                        for analysis in analyses
                    ],
                    "findings": [finding.as_dict() for finding in findings],
                    "gate": "fail" if failed else "pass",
                },
                indent=2,
                sort_keys=True,
            )
        )
    else:
        print(_BANNER)
        print(
            f"profile: {args.profile}"
            + (f" targets: {','.join(dict.fromkeys(args.target))}" if args.target else "")
        )
        for analysis in analyses:
            print(f"query: {analysis.expression}")
            features = ", ".join(analysis.features) if analysis.features else "core PromQL"
            print(f"  features: {features}")
            print(f"  fan-out: {analysis.cost_score} ({analysis.cost_tier})")
        lines = _render_findings(findings)
        print("\n".join(lines) if lines else "no findings")
        print(_summary(findings, analyses[0]))
        print(f"gate: {'FAIL' if failed else 'PASS'} (fail-on {args.fail_on})")
    return 1 if failed else 0


def _render_vector(result: dict[str, object]) -> list[str]:
    lines: list[str] = []
    entries = result.get("result")
    if isinstance(entries, list):
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            labels = entry.get("metric", {})
            rendered = (
                ",".join(f'{key}="{value}"' for key, value in sorted(labels.items()))
                if isinstance(labels, dict)
                else ""
            )
            value = entry.get("value")
            formatted = (
                format_value(float(value[1]))
                if isinstance(value, list) and len(value) == 2
                else "?"
            )
            lines.append(f"  {{{rendered}}} => {formatted}")
    return lines


def _run_eval(args: argparse.Namespace) -> int:
    fixture = load_fixture(args.fixture)
    evaluator = Evaluator(
        fixture,
        args.at,
        lookback_s=args.lookback,
        scrape_interval_s=_duration(args.scrape_interval),
    )
    result = evaluator.as_api(evaluator.evaluate(parse(args.expression)))
    if args.format == "json":
        print(
            json.dumps(
                {
                    "attribution": ATTRIBUTION,
                    "version": __version__,
                    "expression": args.expression,
                    "at": args.at,
                    "result": result,
                },
                indent=2,
                sort_keys=True,
            )
        )
    else:
        print(_BANNER)
        print(f"at: {args.at:g}s  resultType: {result['resultType']}")
        lines = _render_vector(result)
        print("\n".join(lines) if lines else "  <empty result>")
    return 0


def _run_verify(args: argparse.Namespace) -> int:
    pack = load_pack(args.pack)
    findings: list[Finding] = []
    statuses: list[str] = []
    interval = _duration(args.scrape_interval)
    for query in pack.queries:
        analysis = analyze(query.expression, "prometheus_3", interval)
        mismatches: tuple[str, ...] = ()
        if not any(finding.rule_id == "QO001" for finding in analysis.findings):
            evaluator = Evaluator(pack.fixture, query.at, scrape_interval_s=interval)
            _, mismatches = evaluator.verify(query)
        for finding in analysis.findings:
            if finding.rule_id in {"QO001", "QO008", "QO011", "QO012"}:
                findings.append(finding)
        for mismatch in mismatches:
            findings.append(
                Finding(
                    "QO030",
                    f"{query.name}: {mismatch}",
                    0,
                    query.expression,
                    "fixture",
                    "update the query, fixture or golden result deterministically",
                )
            )
        statuses.append(
            f"  {query.name}: {'FAIL' if mismatches else 'PASS'}"
            + (f" — {mismatches[0]}" if mismatches else "")
        )
    ordered = tuple(sorted(findings, key=lambda finding: finding.sort_key))
    failed = _gate(ordered, args.fail_on)
    if args.format == "sarif":
        print(json.dumps(build_sarif(ordered, __version__), indent=2, sort_keys=True))
    elif args.format == "json":
        print(
            json.dumps(
                {
                    "attribution": ATTRIBUTION,
                    "version": __version__,
                    "pack_queries": len(pack.queries),
                    "findings": [finding.as_dict() for finding in ordered],
                    "gate": "fail" if failed else "pass",
                },
                indent=2,
                sort_keys=True,
            )
        )
    else:
        print(_BANNER)
        print(f"pack queries: {len(pack.queries)}")
        print("\n".join(statuses))
        lines = _render_findings(ordered)
        print("\n".join(lines) if lines else "no findings")
        print(_summary(ordered))
        print(f"gate: {'FAIL' if failed else 'PASS'} (fail-on {args.fail_on})")
    return 1 if failed else 0


def _run_profiles(args: argparse.Namespace) -> int:
    document = {
        "attribution": ATTRIBUTION,
        "version": __version__,
        "feature_descriptions": FEATURES,
        "profiles": {name: sorted(PROFILES[name]) for name in profile_names()},
    }
    if args.format == "json":
        print(json.dumps(document, indent=2, sort_keys=True))
    else:
        print(_BANNER)
        for name in profile_names():
            print(f"{name}: {len(PROFILES[name])} verified features")
    return 0


def _run_rules(args: argparse.Namespace) -> int:
    if args.format == "json":
        print(
            json.dumps(
                {"attribution": ATTRIBUTION, "rules": RULE_CATALOG}, indent=2, sort_keys=True
            )
        )
    else:
        print(_BANNER)
        for rule in RULE_CATALOG:
            print(f"{rule['id']} {rule['severity']:<7} {rule['title']}")
    return 0


HANDLERS = {
    "check": _run_check,
    "eval": _run_eval,
    "verify": _run_verify,
    "profiles": _run_profiles,
    "rules": _run_rules,
}


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point with deterministic exit codes."""
    parser = build_parser()
    args = parser.parse_args(argv)
    if getattr(args, "format", None) == "sarif" and args.command not in {"check", "verify"}:
        parser.error("--format sarif is only available for check and verify")
    try:
        return HANDLERS[args.command](args)
    except QueryOracleError as error:
        print(f"{_BANNER}\nerror: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
