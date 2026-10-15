"""
Tests for the PR body generator.

The PR body is what the approver reads: it has to work even when the run failed
halfway through (no junit, no manifest) and it has to expose the risks visibly.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

HUB = Path(__file__).resolve().parents[1]
SCRIPTS = HUB / "scripts"

if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import write_pr_body  # noqa: E402

MANIFEST = {
    "iflow": "FlowA",
    "artifacts": {"scenarios": 30, "risks": 2, "testdata_files": 13},
}

JUNIT = """<?xml version="1.0" encoding="utf-8"?>
<testsuites>
  <testsuite name="pytest" errors="1" failures="2" skipped="3" tests="10" time="1.0">
    <testcase classname="c" name="t"/>
  </testsuite>
</testsuites>
"""


def _evidence(base: Path, name: str, *, junit: bool = True, risks: list | None = None) -> Path:
    """
    Reproduces the layout created by `actions/download-artifact`:

        .evidencias/<artifact>/tests/.../manifest.json
    """
    artifact = base / name
    reports = artifact / "reports" / "Pacote" / "FlowA"
    plans = artifact / "test-plans" / "Pacote" / "FlowA"
    reports.mkdir(parents=True)
    plans.mkdir(parents=True)

    (reports / "manifest.json").write_text(json.dumps(MANIFEST), encoding="utf-8")
    if junit:
        (reports / "junit.xml").write_text(JUNIT, encoding="utf-8")
    if risks is not None:
        (plans / "risks.json").write_text(json.dumps(risks), encoding="utf-8")

    return artifact


# ---------------------------------------------------------------------------
# Counting from the junit
# ---------------------------------------------------------------------------


def test_counts_totals_from_junit(tmp_path: Path):
    path = tmp_path / "junit.xml"
    path.write_text(JUNIT, encoding="utf-8")

    total, passed, failed, error, skipped = write_pr_body.count_junit(path)

    assert (total, failed, error, skipped) == (10, 2, 1, 3)
    assert passed == 10 - 2 - 1 - 3


def test_missing_junit_returns_dashes(tmp_path: Path):
    assert write_pr_body.count_junit(tmp_path / "does_not_exist.xml") == ("-",) * 5


def test_invalid_junit_returns_dashes(tmp_path: Path):
    broken = tmp_path / "junit.xml"
    broken.write_text("<testsuite", encoding="utf-8")

    assert write_pr_body.count_junit(broken) == ("-",) * 5


# ---------------------------------------------------------------------------
# PR body
# ---------------------------------------------------------------------------


def test_body_lists_the_iflow_with_counts(tmp_path: Path):
    _evidence(tmp_path, "results-Pacote-FlowA")

    body = write_pr_body.build_body(tmp_path, "a" * 40)

    assert "Unified test plan" in body
    assert f"`{'a' * 40}`" in body
    assert "| `FlowA` | 10 | 4 | 2 | 1 | 3 |" in body


def test_body_includes_the_review_checklist(tmp_path: Path):
    _evidence(tmp_path, "results-Pacote-FlowA")

    body = write_pr_body.build_body(tmp_path, None)

    assert "## What to review" in body
    assert "AI-generated code that runs in CI" in body


def test_body_without_commit_omits_the_line(tmp_path: Path):
    _evidence(tmp_path, "results-Pacote-FlowA")

    body = write_pr_body.build_body(tmp_path, None)

    assert "Commit:" not in body


def test_body_without_evidence_is_still_valid(tmp_path: Path):
    """A run that failed before generating any artifact still produces a body."""
    body = write_pr_body.build_body(tmp_path, "b" * 40)

    assert "(no evidence found)" in body
    assert "## What to review" in body


def test_body_with_manifest_but_without_junit(tmp_path: Path):
    _evidence(tmp_path, "results-Pacote-FlowA", junit=False)

    body = write_pr_body.build_body(tmp_path, None)

    assert "| `FlowA` | - | - | - | - | - |" in body


def test_body_lists_risks_in_a_collapsible_block(tmp_path: Path):
    _evidence(
        tmp_path,
        "results-Pacote-FlowA",
        risks=[
            {"risk": "Payload loss", "mitigation": "Enable persistence"},
            {"risk": "Timeout in the receiver", "mitigation": "Increase timeout"},
        ],
    )

    body = write_pr_body.build_body(tmp_path, None)

    assert "Risks of <code>FlowA</code> (2)" in body
    assert "Payload loss" in body
    assert "Enable persistence" in body


def test_body_accepts_legacy_portuguese_risk_keys(tmp_path: Path):
    _evidence(
        tmp_path,
        "results-Pacote-FlowA",
        risks=[{"risco": "Mapping failure", "mitigacao": "Review the mmap"}],
    )

    body = write_pr_body.build_body(tmp_path, None)

    assert "Mapping failure" in body
    assert "Review the mmap" in body


def test_body_with_multiple_iflows(tmp_path: Path):
    _evidence(tmp_path, "results-Pacote-FlowA")
    other = tmp_path / "results-Pacote-FlowB" / "reports" / "Pacote" / "FlowB"
    other.mkdir(parents=True)
    (other / "manifest.json").write_text(
        json.dumps({"iflow": "FlowB", "artifacts": {}}), encoding="utf-8"
    )

    body = write_pr_body.build_body(tmp_path, None)

    assert "| `FlowA` |" in body
    assert "| `FlowB` | - | - | - | - | - |" in body


def test_body_ignores_an_invalid_manifest(tmp_path: Path):
    artifact = tmp_path / "results-Pacote-FlowA" / "reports" / "Pacote" / "FlowA"
    artifact.mkdir(parents=True)
    (artifact / "manifest.json").write_text("{ invalid", encoding="utf-8")

    body = write_pr_body.build_body(tmp_path, None)

    # Without a readable iFlow, the row uses the manifest directory name.
    assert "| `FlowA` | - | - | - | - | - |" in body


def test_body_includes_suggested_fixes_when_present(tmp_path: Path):
    """
    The suggested fixes are written only when the suite failed. They say what is
    probably wrong in the iFlow, so they belong where the reviewer is looking.
    """
    _evidence(tmp_path, "results-Pacote-FlowA")
    (tmp_path / "suggestions-SI_FlowA.md").write_text(
        "### Suggested fixes for `SI_FlowA`\n\n"
        "The Groovy condition script must navigate by local names.",
        encoding="utf-8",
    )

    body = write_pr_body.build_body(tmp_path, None)

    assert "Suggested fixes" in body
    assert "navigate by local names" in body


def test_body_omits_suggestions_when_absent(tmp_path: Path):
    """A passing suite produces no suggestion file, and the body must not imply one."""
    _evidence(tmp_path, "results-Pacote-FlowA")

    body = write_pr_body.build_body(tmp_path, None)

    assert "Suggested fixes" not in body


def test_body_ignores_an_empty_suggestion_file(tmp_path: Path):
    _evidence(tmp_path, "results-Pacote-FlowA")
    (tmp_path / "suggestions-SI_FlowA.md").write_text("   \n", encoding="utf-8")

    body = write_pr_body.build_body(tmp_path, None)

    assert "Suggested fixes" not in body


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPTS / "write_pr_body.py"), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def test_cli_writes_the_file(tmp_path: Path):
    _evidence(tmp_path, "results-Pacote-FlowA")
    destination = tmp_path / "body.md"

    result = _run(
        "--evidence-dir", str(tmp_path), "--commit", "c" * 40, "--output", str(destination)
    )

    assert result.returncode == 0
    assert "Unified test plan" in destination.read_text(encoding="utf-8")


def test_cli_prints_to_stdout(tmp_path: Path):
    _evidence(tmp_path, "results-Pacote-FlowA")

    result = _run("--evidence-dir", str(tmp_path))

    assert result.returncode == 0
    assert "## What to review" in result.stdout


def test_cli_missing_directory_returns_exit_2(tmp_path: Path):
    result = _run("--evidence-dir", str(tmp_path / "does_not_exist"))

    assert result.returncode == 2
    assert "not found" in result.stderr
