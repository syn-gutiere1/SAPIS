#!/usr/bin/env python3
"""
summarize.py - generates the run summary for the GitHub Actions Job Summary.

The pytest `.html` file goes as an artifact (the team downloads it), but the Job
Summary appears on the run page itself, without a download. This script reads:

  - `manifest.json` (provenance: iflow, model, prompt, tokens, truncation);
  - `junit.xml` (per-test result, when available);
  - `pytest_logs.txt` (to extract the excerpt of the failures).

and writes Markdown to stdout. The workflow runs:

    python scripts/summarize.py --manifest ... --junit ... >> "$GITHUB_STEP_SUMMARY"

Exit codes:
    0  summary generated (even without all inputs)
    2  no input provided
"""

from __future__ import annotations

import argparse
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

# Limit of log lines shown per failure, so the summary does not become the whole log.
MAX_FAILURE_LINES = 25
MAX_FAILURES_LISTED = 10


def _load_manifest(path: Path) -> dict | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"> Warning: invalid `manifest.json` at `{path}`: {exc}", file=sys.stderr)
        return None


def parse_junit(path: Path) -> dict | None:
    """
    Reads the summary of a pytest `junit.xml`.

    Returns `{"tests", "failures", "errors", "skipped", "time", "cases": [...]}`.
    Accepts both `<testsuites>` (multiple files) and `<testsuite>`.
    """
    if not path.is_file():
        return None

    try:
        root = ET.parse(path).getroot()
    except ET.ParseError as exc:
        print(f"> Warning: invalid `junit.xml` at `{path}`: {exc}", file=sys.stderr)
        return None

    suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))

    totals = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0, "time": 0.0}
    cases: list[dict] = []

    for suite in suites:
        for key in ("tests", "failures", "errors", "skipped"):
            totals[key] += int(suite.get(key, 0) or 0)
        try:
            totals["time"] += float(suite.get("time", 0) or 0)
        except ValueError:
            pass

        for case in suite.iter("testcase"):
            entry = {
                "name": case.get("name", "?"),
                "classname": case.get("classname", ""),
                "status": "passed",
            }
            failure = case.find("failure")
            error = case.find("error")
            skipped = case.find("skipped")

            if failure is not None:
                entry["status"] = "failed"
                entry["message"] = (failure.get("message") or "")[:500]
                entry["detail"] = (failure.text or "").strip()
            elif error is not None:
                entry["status"] = "error"
                entry["message"] = (error.get("message") or "")[:500]
                entry["detail"] = (error.text or "").strip()
            elif skipped is not None:
                entry["status"] = "skipped"

            cases.append(entry)

    return {**totals, "cases": cases}


def _summarize_failures(cases: list[dict]) -> list[str]:
    """Markdown block with the detail of the first failures."""
    failed = [c for c in cases if c["status"] in ("failed", "error")]
    if not failed:
        return []

    lines = ["", f"### Failures ({len(failed)})", ""]
    for case in failed[:MAX_FAILURES_LISTED]:
        title = case["name"]
        if case["classname"]:
            title = f"{case['classname']}::{case['name']}"
        lines.append(f"<details><summary><code>{title}</code></summary>")
        lines.append("")
        if case.get("message"):
            lines.append(f"```\n{case['message']}\n```")
        detail = case.get("detail") or ""
        if detail:
            excerpt = "\n".join(detail.splitlines()[:MAX_FAILURE_LINES])
            lines.append(f"```\n{excerpt}\n```")
        lines += ["", "</details>", ""]

    if len(failed) > MAX_FAILURES_LISTED:
        lines.append(
            f"_...and {len(failed) - MAX_FAILURES_LISTED} more failure(s); "
            "see the artifact with the full report._"
        )

    return lines


def _log_excerpt(path: Path, max_lines: int = MAX_FAILURE_LINES) -> list[str]:
    """Last lines of the pytest log, for when there is no junit."""
    if not path.is_file():
        return []
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []

    tail = [line for line in content.splitlines() if line.strip()][-max_lines:]
    if not tail:
        return []
    return ["", "### End of the pytest log", "", "```", *tail, "```"]


def render(
    *,
    manifest: dict | None,
    junit: dict | None,
    logs: Path | None,
    iflow: str | None,
    artifact_name: str | None,
) -> str:
    """Builds the summary in Markdown."""
    name = iflow or (manifest or {}).get("iflow") or "iFlow"
    lines = [f"## Unified test plan — `{name}`", ""]

    # --- test result ------------------------------------------------------
    if junit and junit["tests"]:
        total = junit["tests"]
        passed = total - junit["failures"] - junit["errors"] - junit["skipped"]
        if junit["failures"] or junit["errors"]:
            verdict = "🔴 failed"
        elif junit["skipped"] == total:
            verdict = "⚪ all skipped"
        else:
            verdict = "🟢 passed"

        lines += [
            f"**Result:** {verdict}",
            "",
            "| Tests | Passed | Failed | Error | Skipped | Time |",
            "|---|---|---|---|---|---|",
            f"| {total} | {passed} | {junit['failures']} | {junit['errors']} | "
            f"{junit['skipped']} | {junit['time']:.2f}s |",
        ]
    elif junit is None:
        lines.append("_Test result not available (junit.xml missing)._")

    # --- provenance -------------------------------------------------------
    if manifest:
        llm = manifest.get("llm") or {}
        artifacts = manifest.get("artifacts") or {}
        prompt_info = manifest.get("prompt") or {}
        environment = manifest.get("environment") or {}

        def _value(value, default: str = "-") -> str:
            return default if value is None else str(value)

        def _hash_prefix(value) -> str:
            """First 12 characters, or '-' when there is no hash (generation skipped)."""
            if not value:
                return "-"
            return f"`{str(value)[:12]}…`"

        lines += [
            "",
            "### Provenance",
            "",
            "| Item | Value |",
            "|---|---|",
            f"| iFlow | `{manifest.get('iflow')}` |",
            f"| Commit | `{str(environment.get('github_sha'))[:12]}` |",
            f"| Model | `{_value(llm.get('model'))}` |",
            f"| Scenarios | {_value(artifacts.get('scenarios'))} |",
            f"| Risks | {_value(artifacts.get('risks'))} |",
            f"| Fixtures | {_value(artifacts.get('testdata_files'))} |",
            f"| Scripts analyzed | {_value(manifest.get('scripts_count'))} |",
            f"| Tokens | {_value(llm.get('total_tokens'))} |",
            f"| Attempts | {_value(manifest.get('attempts'))} |",
            f"| `prompt_sha256` | {_hash_prefix(prompt_info.get('sha256'))} |",
            f"| `iflow_sha256` | {_hash_prefix(manifest.get('iflow_sha256'))} |",
        ]

        if manifest.get("truncated"):
            lines += [
                "",
                "> ⚠️ **Content truncated before submission** — the coverage may have "
                f"gaps: {', '.join(manifest['truncated'])}",
            ]

        rejected = artifacts.get("rejected") or []
        if rejected:
            lines += ["", "### Discarded fixtures", ""]
            lines += [f"- {item}" for item in rejected]
    else:
        # Without a manifest the run is still reportable: it happens when the
        # generation was skipped because the `.iflw` did not change, or when it
        # failed before writing.
        lines += [
            "",
            "> No `manifest.json`: the generation did not run in this run "
            "(probably the artifact's `.iflw` did not change). "
            "The results below are from the tests already versioned.",
        ]

    if artifact_name:
        lines += ["", f"📦 Full HTML report in artifact **`{artifact_name}`**."]

    if junit:
        lines += _summarize_failures(junit["cases"])
    elif logs is not None:
        lines += _log_excerpt(logs)

    return "\n".join(lines) + "\n"


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generates the test run summary for the GitHub Actions Job Summary."
    )
    parser.add_argument("--manifest", default=None, help="Path to manifest.json.")
    parser.add_argument("--junit", default=None, help="Path to the pytest junit.xml.")
    parser.add_argument("--logs", default=None, help="Path to pytest_logs.txt.")
    parser.add_argument("--iflow", default=None, help="iFlow name (when there is no manifest).")
    parser.add_argument("--artifact-name", default=None, help="Name of the published artifact.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    # The summary uses emoji and non-ASCII characters. On Windows stdout is usually
    # cp1252, which breaks the write; on the GitHub runner it is UTF-8. We force
    # UTF-8 with a tolerant fallback so the script runs in both environments.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):  # pragma: no cover - stdout redirected
        pass

    args = _parse_args(argv)

    if not any((args.manifest, args.junit, args.logs)):
        print(
            "[ERROR] Provide at least one of --manifest, --junit or --logs.",
            file=sys.stderr,
        )
        return 2

    manifest = _load_manifest(Path(args.manifest)) if args.manifest else None
    junit = parse_junit(Path(args.junit)) if args.junit else None

    markdown = render(
        manifest=manifest,
        junit=junit,
        logs=Path(args.logs) if args.logs else None,
        iflow=args.iflow,
        artifact_name=args.artifact_name,
    )
    print(markdown, end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
