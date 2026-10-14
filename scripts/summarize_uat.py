#!/usr/bin/env python3
"""
summarize_uat.py - UAT summary for the Job Summary and for the Release.

Difference from `summarize.py` in the test flow: here the tests touch real
systems, so the summary must make explicit the environment, the approved
commit and who approved it. It is the evidence that answers "what was
validated, in which version, and against which environment".

Exit codes: 0 = summary generated (even without junit), 2 = no input provided.
"""

from __future__ import annotations

import argparse
import os
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

# Reuses the junit parsing of the main summary, so there are not two
# implementations of the same parse.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from summarize import parse_junit  # noqa: E402


def render(
    *,
    junit: dict | None,
    environment: str,
    commit: str | None,
    approver: str | None,
    run_url: str | None,
    has_tests: bool = True,
) -> str:
    lines = ["## UAT evidence", ""]

    if not has_tests:
        lines += [
            "> No UAT test versioned under `tests/uat`. The gate was approved, "
            "but there was no real execution.",
            "",
        ]

    lines += [
        "| Item | Value |",
        "|---|---|",
        f"| Environment | `{environment}` |",
        f"| Commit | `{(commit or '-')[:12]}` |",
        f"| Approved by (reviewer) | {('@' + approver) if approver else '-'} |",
    ]
    if run_url:
        lines.append(f"| Workflow run | {run_url} |")

    if junit and junit["tests"]:
        total = junit["tests"]
        passed = total - junit["failures"] - junit["errors"] - junit["skipped"]
        verdict = "🔴 failed" if (junit["failures"] or junit["errors"]) else "🟢 passed"

        lines += [
            "",
            f"**Result:** {verdict}",
            "",
            "| Tests | Passed | Failed | Error | Skipped | Time |",
            "|---|---|---|---|---|---|",
            f"| {total} | {passed} | {junit['failures']} | {junit['errors']} | "
            f"{junit['skipped']} | {junit['time']:.2f}s |",
        ]

        failures = [c for c in junit["cases"] if c["status"] in ("failed", "error")]
        if failures:
            lines += ["", f"### Failures ({len(failures)})", ""]
            for case in failures[:10]:
                title = f"{case['classname']}::{case['name']}" if case["classname"] else case["name"]
                lines.append(f"- `{title}`")
                if case.get("message"):
                    lines.append(f"  - {case['message'][:300]}")
    else:
        lines += ["", "_No test results available._"]

    return "\n".join(lines) + "\n"


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="UAT summary for the Job Summary.")
    parser.add_argument("--junit", default=None, help="Path to uat-junit.xml.")
    parser.add_argument("--environment", default="uat", help="Environment name.")
    parser.add_argument("--commit", default=None, help="SHA of the validated commit.")
    parser.add_argument("--ator", default=None, help="Who triggered/approved. Default: github.actor.")
    parser.add_argument("--run-url", default=None, help="URL of the workflow run.")
    parser.add_argument("--tem-testes", default="true", help="'false' when there is no tests/uat.")
    parser.add_argument("--output", default=None, help="Output file (default: stdout).")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):  # pragma: no cover
        pass

    args = _parse_args(argv)

    # `--tem-testes` and `--ator` are part of the CLI interface and keep their
    # Portuguese names; only the local variables are English.
    has_tests = args.tem_testes.lower() != "false"

    if not args.junit and has_tests:
        print("[ERROR] provide --junit (or --tem-testes false).", file=sys.stderr)
        return 2

    junit = parse_junit(Path(args.junit)) if args.junit else None

    approver = args.ator or os.environ.get("GITHUB_ACTOR")
    if not args.run_url:
        repo = os.environ.get("GITHUB_REPOSITORY")
        run_id = os.environ.get("GITHUB_RUN_ID")
        if repo and run_id:
            args.run_url = f"https://github.com/{repo}/actions/runs/{run_id}"

    markdown = render(
        junit=junit,
        environment=args.environment,
        commit=args.commit or os.environ.get("GITHUB_SHA"),
        approver=approver,
        run_url=args.run_url,
        has_tests=has_tests,
    )

    if args.output:
        Path(args.output).write_text(markdown, encoding="utf-8")
    else:
        print(markdown, end="")

    return 0


if __name__ == "__main__":
    sys.exit(main())
