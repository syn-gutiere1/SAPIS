"""
Tests for the UAT summary.

The UAT summary is the evidence: it must answer "what was validated, in which
commit, against which environment and who approved it". That is why the tests
check those fields, and not only the counts.
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

import summarize_uat  # noqa: E402

JUNIT_OK = """<?xml version="1.0" encoding="utf-8"?>
<testsuites>
  <testsuite name="pytest" errors="0" failures="0" skipped="1" tests="4" time="12.5">
    <testcase classname="test_uat_smoke" name="test_endpoint_responds" time="3.0"/>
    <testcase classname="test_uat_smoke" name="test_soap_contract" time="4.0"/>
    <testcase classname="test_uat_smoke" name="test_invalid_order" time="5.0"/>
    <testcase classname="test_uat_smoke" name="test_without_credential" time="0.5">
      <skipped message="no UAT credential in the environment"/>
    </testcase>
  </testsuite>
</testsuites>
"""

JUNIT_FAILURE = """<?xml version="1.0" encoding="utf-8"?>
<testsuites>
  <testsuite name="pytest" errors="0" failures="1" skipped="0" tests="2" time="8.0">
    <testcase classname="test_uat_smoke" name="test_ok" time="1.0"/>
    <testcase classname="test_uat_smoke" name="test_creates_order" time="7.0">
      <failure message="HTTP 500 from the UAT endpoint">E   assert 500 == 202</failure>
    </testcase>
  </testsuite>
</testsuites>
"""


@pytest.fixture
def junit_ok(tmp_path: Path) -> Path:
    junit_path = tmp_path / "uat-junit.xml"
    junit_path.write_text(JUNIT_OK, encoding="utf-8")
    return junit_path


@pytest.fixture
def junit_failure(tmp_path: Path) -> Path:
    junit_path = tmp_path / "uat-junit.xml"
    junit_path.write_text(JUNIT_FAILURE, encoding="utf-8")
    return junit_path


# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------


def test_render_records_environment_commit_and_approver(junit_ok: Path):
    text = summarize_uat.render(
        junit=summarize_uat.parse_junit(junit_ok),
        environment="uat",
        commit="a" * 40,
        approver="rggutierres",
        run_url="https://github.com/org/repo/actions/runs/1",
    )

    assert "| Environment | `uat` |" in text
    assert f"| Commit | `{'a' * 12}` |" in text
    assert "@rggutierres" in text
    assert "actions/runs/1" in text


def test_render_shows_success_verdict(junit_ok: Path):
    text = summarize_uat.render(
        junit=summarize_uat.parse_junit(junit_ok),
        environment="uat",
        commit="b" * 40,
        approver=None,
        run_url=None,
    )

    assert "🟢 passed" in text
    assert "| 4 | 3 | 0 | 0 | 1 |" in text


def test_render_shows_failure_verdict(junit_failure: Path):
    text = summarize_uat.render(
        junit=summarize_uat.parse_junit(junit_failure),
        environment="uat",
        commit="c" * 40,
        approver="qa",
        run_url=None,
    )

    assert "🔴 failed" in text
    assert "### Failures (1)" in text
    assert "test_uat_smoke::test_creates_order" in text


def test_render_without_approver_does_not_print_none(junit_ok: Path):
    text = summarize_uat.render(
        junit=summarize_uat.parse_junit(junit_ok),
        environment="uat",
        commit=None,
        approver=None,
        run_url=None,
    )

    assert "None" not in text
    assert "| Commit | `-` |" in text


def test_render_without_versioned_tests_warns(junit_ok: Path):
    """
    The gate can be approved with no UAT test versioned. The evidence cannot
    suggest that anything was validated.
    """
    text = summarize_uat.render(
        junit=None,
        environment="uat",
        commit="d" * 40,
        approver="qa",
        run_url=None,
        has_tests=False,
    )

    assert "No UAT test versioned" in text
    assert "no real execution" in text


def test_render_missing_junit_differs_from_no_tests():
    """`has_tests=True` with junit missing means a run without a report."""
    text = summarize_uat.render(
        junit=None,
        environment="uat",
        commit="e" * 40,
        approver="qa",
        run_url=None,
        has_tests=True,
    )

    assert "No UAT test versioned" not in text
    assert "No test results available" in text


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPTS / "summarize_uat.py"), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def test_cli_renders(junit_ok: Path):
    result = _run("--junit", str(junit_ok), "--environment", "uat", "--commit", "f" * 40)

    assert result.returncode == 0
    assert "## UAT evidence" in result.stdout


def test_cli_without_arguments_returns_two():
    result = _run()

    assert result.returncode == 2


def test_cli_without_tests_does_not_require_junit():
    result = _run("--has-tests", "false", "--commit", "a" * 40)

    assert result.returncode == 0
    assert "No UAT test versioned" in result.stdout


def test_cli_reads_environment_from_github(monkeypatch, junit_ok: Path):
    monkeypatch.setenv("GITHUB_ACTOR", "approver")
    monkeypatch.setenv("GITHUB_SHA", "9" * 40)
    monkeypatch.setenv("GITHUB_REPOSITORY", "org/repo")
    monkeypatch.setenv("GITHUB_RUN_ID", "42")

    result = _run("--junit", str(junit_ok))

    assert "@approver" in result.stdout
    assert "runs/42" in result.stdout
    assert f"`{'9' * 12}`" in result.stdout


def test_cli_writes_file(tmp_path: Path, junit_ok: Path):
    output_path = tmp_path / "summary.md"

    result = _run("--junit", str(junit_ok), "--commit", "a" * 40, "--output", str(output_path))

    assert result.returncode == 0
    assert "UAT evidence" in output_path.read_text(encoding="utf-8")
