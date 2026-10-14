#!/usr/bin/env python3
"""
write_pr_body.py - builds the PR body from the generated evidence.

Reads the directory where the artifacts were downloaded. Each iFlow contributes a
`manifest.json` and, when present, a `junit.xml` next to it.

Usage:

    python write_pr_body.py --evidence-dir .evidencias --commit "$GITHUB_SHA" \\
                            --output corpo-pr.md

Exit codes:
    0  body generated
    2  evidence directory does not exist
"""

from __future__ import annotations

import argparse
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

CHECKLIST = """## What to review

- [ ] Scenarios in `test-plans/**/cases.json` make sense for the iFlow
- [ ] Risks in `test-plans/**/risks.json` were assessed
- [ ] `testdata/**` contains no personal or real data
- [ ] `test_flow.py` was read — **it is AI-generated code that runs in CI**
- [ ] If the iFlow has `.groovy`, the real behavior was executed (not just simulated)

> Full HTML reports are in the **Artifacts** tab of this run.
"""

TABLE_HEADER = (
    "| iFlow | Tests | Passed | Failed | Error | Skipped |",
    "|---|---|---|---|---|---|",
)


def count_junit(junit_path: Path) -> tuple[int | str, ...]:
    """
    Reads the totals from a `junit.xml`.

    Returns `(total, passed, failed, error, skipped)`. When the file does not
    exist, returns `"-"` in every position - a run that failed before pytest still
    needs to produce a PR body.
    """
    if not junit_path.is_file():
        return ("-", "-", "-", "-", "-")

    try:
        root = ET.parse(junit_path).getroot()
    except ET.ParseError:
        return ("-", "-", "-", "-", "-")

    suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
    total = sum(int(s.get("tests", 0) or 0) for s in suites)
    failed = sum(int(s.get("failures", 0) or 0) for s in suites)
    errors = sum(int(s.get("errors", 0) or 0) for s in suites)
    skipped = sum(int(s.get("skipped", 0) or 0) for s in suites)
    return (total, total - failed - errors - skipped, failed, errors, skipped)


def _iflow_name(manifest: dict, path: Path) -> str:
    """
    iFlow name, with fallbacks.

    The manifest may not carry `iflow` (run without generation, or partial write).
    In that case we use the manifest directory name, which follows the
    `reports/<Pacote>/<iFlow>/` layout, instead of printing "None" in the PR body.
    """
    name = manifest.get("iflow")
    if name:
        return str(name)
    return path.parent.name or "?"


def _table_row(manifest: dict, path: Path, junit_path: Path) -> str:
    total, passed, failed, errors, skipped = count_junit(junit_path)
    name = _iflow_name(manifest, path)
    if manifest.get("generation_skipped"):
        name = f"{name} (generation skipped)"
    return f"| `{name}` | {total} | {passed} | {failed} | {errors} | {skipped} |"


def _risks_section(manifest: dict, path: Path, plans_dir: Path) -> list[str]:
    """Highlights the highest-value risks for the PR reviewer."""
    risks_file = plans_dir / "risks.json"
    if not risks_file.is_file():
        return []
    try:
        risks = json.loads(risks_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    if not isinstance(risks, list) or not risks:
        return []

    name = _iflow_name(manifest, path)
    lines = ["", f"<details><summary>Risks of <code>{name}</code> "
                  f"({len(risks)})</summary>", ""]
    for risk in risks[:20]:
        if isinstance(risk, dict):
            text = risk.get("risk") or risk.get("risco") or str(risk)
            mitigation = risk.get("mitigation") or risk.get("mitigacao") or ""
            lines.append(f"- **{text}**")
            if mitigation:
                lines.append(f"  - Mitigation: {mitigation}")
        else:
            lines.append(f"- {risk}")
    lines += ["", "</details>"]
    return lines


def build_body(evidence_dir: Path, commit: str | None, plans_root: Path | None = None) -> str:
    """
    Builds the full PR body.

    `plans_root` allows passing the root of `test-plans/` to locate the
    risks. When omitted, it looks for `risks.json` by walking up from the manifest
    directory - which works with the artifact layout and with a normal repository
    checkout.
    """
    manifests = sorted(evidence_dir.rglob("manifest.json"))

    lines = ["# Unified test plan", ""]
    if commit:
        lines += [f"Commit: `{commit}`", ""]
    lines += ["## Evidence per iFlow", "", *TABLE_HEADER]

    if not manifests:
        lines.append("| _(no evidence found)_ | - | - | - | - | - |")
    else:
        for path in manifests:
            try:
                manifest = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                lines.append(f"| `{path.parent.name}` | - | - | - | - | - |")
                continue
            lines.append(_table_row(manifest, path, path.parent / "junit.xml"))
            lines += _risks_section(
                manifest, path, _locate_plans(path, plans_root)
            )

    lines += ["", CHECKLIST]
    return "\n".join(lines) + "\n"


def _locate_plans(manifest_path: Path, plans_root: Path | None) -> Path:
    """
    Determines the `test-plans` directory that corresponds to a manifest.

    Strategy: walk up from the manifest looking for a `test-plans` folder
    and descend through the relative path `Pacote/<iFlow>`. If it is not found, it
    returns a nonexistent path - the risks section simply does not appear.
    """
    if plans_root is not None:
        return plans_root

    # The manifest lives in reports/<Pacote>/<iFlow>/manifest.json; the package and
    # the iFlow are the last two components of the parent directory.
    iflow_dir = manifest_path.parent
    package_dir = iflow_dir.parent

    for ancestor in manifest_path.parents:
        candidate = ancestor / "test-plans" / package_dir.name / iflow_dir.name
        if candidate.is_dir():
            return candidate

    return manifest_path.parent / "test-plans" / "__inexistente__"


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Builds the PR body from the generated evidence.")
    parser.add_argument("--evidence-dir", required=True, help="Directory with the downloaded artifacts.")
    parser.add_argument("--commit", default=None, help="Commit SHA, shown in the body.")
    parser.add_argument("--output", default=None, help="Output file (default: stdout).")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    evidence_dir = Path(args.evidence_dir)
    if not evidence_dir.is_dir():
        print(f"[ERROR] evidence directory not found: {evidence_dir}", file=sys.stderr)
        return 2

    body = build_body(evidence_dir, args.commit)

    if args.output:
        Path(args.output).write_text(body, encoding="utf-8")
        print(f"PR body written to {args.output}")
    else:
        print(body, end="")

    return 0


if __name__ == "__main__":
    sys.exit(main())
