"""
Tests for `build_dashboard.py`.

The dashboard is the page a reviewer actually opens, so the tests check that it is
self-contained, that each section reflects its input, and - most importantly - that
a missing input is reported as missing instead of rendering an empty section that
looks like data.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"

if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import build_dashboard  # noqa: E402

SCENARIOS = [
    {
        "id": "TC-001",
        "name": "Valid order creates the SO order",
        "trigger": "SOAP notification",
        "expected_outcome": "202 from CreateOrder",
        "validates": "happy path",
    },
    {"id": "TC-002", "name": "Unknown code escalates"},
]

RISKS = [
    {
        "risk": "Namespace misalignment",
        "consequence": "every order rejected",
        "mitigation": "navigate by local names",
    }
]

COVERAGE = {
    "target_coverage_percent": 100,
    "coverage_by_component": {"routing": "80%", "mapping": 60},
    "uncovered_paths": ["exception subprocess"],
    "coverage_recommendations": ["add a malformed payload case"],
}

MANIFEST = {
    "iflow": "FlowA",
    "iflow_sha256": "a" * 64,
    "attempts": 2,
    "duration_seconds": 12.5,
    "generated_at": "2026-01-01T00:00:00Z",
    "llm": {"model": "gemini-x", "prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
    "prompt": {"sha256": "b" * 64},
    "artifacts": {"scenarios": 2, "risks": 1, "testdata_files": 3, "rejected": []},
    "truncated": [],
    "environment": {"github_sha": "c" * 40, "github_run_id": "42"},
}

JUNIT = """<?xml version="1.0" encoding="utf-8"?>
<testsuites>
  <testsuite name="pytest" errors="0" failures="1" skipped="1" tests="3" time="2.5">
    <testcase classname="test_flow.TestA" name="test_ok" time="0.1"/>
    <testcase classname="test_flow.TestB" name="test_bad" time="0.2">
      <failure message="assert False">E   AssertionError</failure>
    </testcase>
    <testcase classname="test_flow.TestC" name="test_skip" time="0.0">
      <skipped message="no fixture"/>
    </testcase>
  </testsuite>
</testsuites>
"""


@pytest.fixture
def artifacts(tmp_path: Path) -> dict[str, Path]:
    import json

    paths = {
        "cases": tmp_path / "cases.json",
        "risks": tmp_path / "risks.json",
        "coverage": tmp_path / "coverage.json",
        "manifest": tmp_path / "manifest.json",
        "junit": tmp_path / "junit.xml",
        "suggestions": tmp_path / "ai_fix_suggestions.md",
    }
    paths["cases"].write_text(json.dumps(SCENARIOS), encoding="utf-8")
    paths["risks"].write_text(json.dumps(RISKS), encoding="utf-8")
    paths["coverage"].write_text(json.dumps(COVERAGE), encoding="utf-8")
    paths["manifest"].write_text(json.dumps(MANIFEST), encoding="utf-8")
    paths["junit"].write_text(JUNIT, encoding="utf-8")
    paths["suggestions"].write_text("# Suggested fixes\n\nFix the namespace.\n", encoding="utf-8")
    return paths


def _page(artifacts: dict[str, Path], **overrides) -> str:
    kwargs = {
        "iflow": "FlowA",
        "scenarios": build_dashboard._load_json(artifacts["cases"]),
        "risks": build_dashboard._load_json(artifacts["risks"]),
        "coverage": build_dashboard._load_json(artifacts["coverage"]),
        "manifest": build_dashboard._load_json(artifacts["manifest"]),
        "junit": build_dashboard.load_junit(artifacts["junit"]),
        "groovy_junit": None,
        "suggestions": build_dashboard._load_text(artifacts["suggestions"]),
    }
    kwargs.update(overrides)
    return build_dashboard.build_page(**kwargs)


def _write(path: Path, content: str) -> Path:
    path.write_text(content, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Self-contained
# ---------------------------------------------------------------------------


def test_page_is_self_contained(artifacts: dict[str, Path]):
    """
    The evidence has to be readable offline - from a downloaded artifact, a
    Release, or an e-mail attachment. No CDN, no external stylesheet, no fetch.
    """
    page = _page(artifacts)

    assert "http://" not in page.replace("http://www.w3.org", "")
    assert "https://" not in page
    assert "<style>" in page
    assert "<script>" in page
    assert "<link" not in page


def test_page_has_one_panel_per_section(artifacts: dict[str, Path]):
    page = _page(artifacts)

    for ident in (
        "tab-overview",
        "tab-scenarios",
        "tab-tests",
        "tab-coverage",
        "tab-risks",
        "tab-fixes",
    ):
        assert f'id="{ident}"' in page


def test_tabs_are_in_reading_order(artifacts: dict[str, Path]):
    """
    The tab order is the reading order: what the run was meant to demonstrate, then the
    results, then the coverage, and finally what still needs action - the risks and the
    suggested fixes.
    """
    page = _page(artifacts)

    labels = re.findall(r'<button role="tab"[^>]*>([^<]*)', page)

    assert labels == [
        "Overview",
        "Scenarios",
        "Tests",
        "Coverage",
        "Risks",
        "Suggested fixes",
    ], labels

    # The panels must follow the buttons. If they diverge, the first tab shown and the
    # first section rendered disagree and the page opens on the wrong content.
    panels = re.findall(r'<section id="(tab-[a-z]+)"', page)
    assert panels == [
        "tab-overview",
        "tab-scenarios",
        "tab-tests",
        "tab-coverage",
        "tab-risks",
        "tab-fixes",
    ], panels


def test_page_escapes_model_content(artifacts: dict[str, Path]):
    """
    The scenarios come from a model that read an untrusted artifact. Nothing it
    produced may be injected as markup.
    """
    hostile = [{"id": "TC-1", "name": "<script>alert('x')</script>"}]
    page = _page(artifacts, scenarios=hostile)

    assert "<script>alert" not in page
    assert "&lt;script&gt;alert" in page


# ---------------------------------------------------------------------------
# Content
# ---------------------------------------------------------------------------


def test_scenarios_are_rendered(artifacts: dict[str, Path]):
    page = _page(artifacts)

    assert "TC-001" in page
    assert "Valid order creates the SO order" in page
    assert "happy path" in page


def test_risks_are_rendered(artifacts: dict[str, Path]):
    page = _page(artifacts)

    assert "Namespace misalignment" in page
    assert "navigate by local names" in page


def test_coverage_bars_and_gaps_are_rendered(artifacts: dict[str, Path]):
    page = _page(artifacts)

    assert "80%" in page
    assert "exception subprocess" in page
    assert "add a malformed payload case" in page


def test_test_results_are_rendered(artifacts: dict[str, Path]):
    page = _page(artifacts)

    assert "test_flow.TestB::test_bad" in page
    assert "AssertionError" in page
    assert "failed" in page


def test_provenance_is_rendered(artifacts: dict[str, Path]):
    page = _page(artifacts)

    assert "gemini-x" in page
    assert "150" in page
    assert "cccccccccccc" in page


def test_suggestions_are_rendered(artifacts: dict[str, Path]):
    page = _page(artifacts)

    assert "Fix the namespace." in page


def test_verdict_reflects_the_junit(artifacts: dict[str, Path]):
    assert "failed" in _page(artifacts)

    passed = build_dashboard.build_page(
        iflow="FlowA",
        scenarios=SCENARIOS,
        risks=RISKS,
        coverage=COVERAGE,
        manifest=MANIFEST,
        junit={"tests": 1, "failures": 0, "errors": 0, "skipped": 0, "time": 0.1, "cases": []},
        suggestions=None,
    )
    assert "passed" in passed


# ---------------------------------------------------------------------------
# Missing inputs
# ---------------------------------------------------------------------------


def test_missing_everything_says_unavailable(artifacts: dict[str, Path]):
    """A section with no data must never look like data."""
    page = build_dashboard.build_page(
        iflow="FlowA",
        scenarios=None,
        risks=None,
        coverage=None,
        manifest=None,
        junit=None,
        suggestions=None,
    )

    assert "No scenarios recorded" in page
    assert "No risks recorded" in page
    assert "No coverage analysis" in page
    assert "No manifest available" in page
    assert "No test report available" in page
    assert "No suggested fixes for this run" in page


# ---------------------------------------------------------------------------
# The two suites are separate verdicts
# ---------------------------------------------------------------------------


def test_both_suites_are_shown_separately(artifacts: dict[str, Path]):
    """
    A green generated suite with a red harness is a failed run. Merging them into one
    number is what let a script that does not even compile read as a passing run.
    """
    groovy = build_dashboard.load_junit(
        _write(
            artifacts["junit"].parent / "junit-groovy.xml",
            """<?xml version="1.0" encoding="utf-8"?>
<testsuites>
  <testsuite name="groovy" errors="0" failures="1" skipped="0" tests="2" time="0.3">
    <testcase classname="Rule.groovy" name="case_a">
      <failure message="expected true, got false"/>
    </testcase>
    <testcase classname="Rule.groovy" name="case_b"/>
  </testsuite>
</testsuites>
""",
        )
    )

    page = _page(artifacts, groovy_junit=groovy)

    assert "Generated suite (pytest)" in page
    assert "Groovy harness (real scripts)" in page
    # The pytest side of the fixture is 3 tests, 1 failure; the harness adds 2/1.
    assert "authoritative verdict" in page
    # Overall verdict must be the worse of the two.
    assert "Verdict: <strong>failed</strong>" in page


def test_verdict_passes_only_when_both_are_green(artifacts: dict[str, Path]):
    green = build_dashboard.load_junit(
        _write(
            artifacts["junit"].parent / "junit-groovy-green.xml",
            """<?xml version="1.0" encoding="utf-8"?>
<testsuites>
  <testsuite name="groovy" errors="0" failures="0" skipped="0" tests="2" time="0.3">
    <testcase classname="Rule.groovy" name="case_a"/>
  </testsuite>
</testsuites>
""",
        )
    )

    both_green = build_dashboard.build_page(
        iflow="FlowA",
        scenarios=None,
        risks=None,
        coverage=None,
        manifest=None,
        junit={"tests": 1, "failures": 0, "errors": 0, "skipped": 0, "time": 0.1, "cases": []},
        groovy_junit=green,
        suggestions=None,
    )

    assert "Verdict: <strong>passed</strong>" in both_green


def test_missing_harness_report_says_so(artifacts: dict[str, Path]):
    """A run where the harness did not execute must not imply that it passed."""
    page = build_dashboard.build_page(
        iflow="FlowA",
        scenarios=None,
        risks=None,
        coverage=None,
        manifest=None,
        junit=build_dashboard.load_junit(artifacts["junit"]),
        groovy_junit=None,
        suggestions=None,
    )

    assert "No report for this suite in the run." in page


def test_truncation_is_surfaced(artifacts: dict[str, Path]):
    manifest = dict(MANIFEST)
    manifest["truncated"] = ["flow_xml (45225 -> 30000 chars)"]

    page = _page(artifacts, manifest=manifest)

    assert "Content truncated before submission" in page
    assert "45225" in page


def test_rejected_fixtures_are_surfaced(artifacts: dict[str, Path]):
    manifest = dict(MANIFEST)
    manifest["artifacts"] = dict(MANIFEST["artifacts"])
    manifest["artifacts"]["rejected"] = ["'payload.py': Extension not allowed"]

    page = _page(artifacts, manifest=manifest)

    assert "Discarded fixtures" in page
    assert "payload.py" in page


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPTS / "build_dashboard.py"), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def test_cli_writes_the_page(artifacts: dict[str, Path], tmp_path: Path):
    destino = tmp_path / "out" / "dashboard.html"

    result = _run(
        "--iflow", "FlowA",
        "--cases", str(artifacts["cases"]),
        "--risks", str(artifacts["risks"]),
        "--coverage", str(artifacts["coverage"]),
        "--manifest", str(artifacts["manifest"]),
        "--junit", str(artifacts["junit"]),
        "--suggestions", str(artifacts["suggestions"]),
        "--output", str(destino),
    )

    assert result.returncode == 0, result.stderr
    assert destino.is_file()
    page = destino.read_text(encoding="utf-8")
    assert "Test evidence — FlowA" in page


def test_cli_works_with_no_optional_inputs(tmp_path: Path):
    """The workflow passes paths that may not exist; the page must still build."""
    destino = tmp_path / "dashboard.html"

    result = _run(
        "--iflow", "FlowA",
        "--cases", str(tmp_path / "missing.json"),
        "--risks", str(tmp_path / "missing.json"),
        "--manifest", str(tmp_path / "missing.json"),
        "--junit", str(tmp_path / "missing.xml"),
        "--output", str(destino),
    )

    assert result.returncode == 0
    assert destino.is_file()


def test_cli_requires_an_output_path():
    result = _run("--iflow", "FlowA")

    assert result.returncode == 2


# ---------------------------------------------------------------------------
# The Fix Risks section
# ---------------------------------------------------------------------------


FIX_RISKS = """# Suggested fixes

### Analysis

The script does not compile.

## Fix Risks

- **Message Loss** - connect CallActivity_86 between ParallelGateway_74 and
  ExclusiveGateway_80.
- **Payload Loss** - initialise the InputPayload property before CallActivity_97.
"""


def test_fix_risks_gets_its_own_heading(artifacts: dict[str, Path]):
    """
    The answer closes with how to fix or mitigate each identified risk. That half is what
    a reviewer acts on after the immediate failure is handled, so it is not left buried in
    the tail of a long document.
    """
    page = _page(artifacts, suggestions=FIX_RISKS)

    assert 'id="fix-risks"' in page
    assert "connect CallActivity_86" in page
    assert "initialise the InputPayload property" in page
    # The analysis is still rendered above it.
    assert "does not compile" in page


def test_fix_risks_is_matched_regardless_of_case(artifacts: dict[str, Path]):
    """The model is asked for `## Fix Risks` but may vary the casing."""
    page = _page(artifacts, suggestions="Analysis.\n\n## FIX RISKS\n\nMitigation text.")

    assert 'id="fix-risks"' in page
    assert "Mitigation text." in page


def test_missing_fix_risks_is_reported(artifacts: dict[str, Path]):
    """
    The model is instructed to emit the section. When it does not, the page says so
    rather than letting the omission pass unnoticed.
    """
    page = _page(artifacts, suggestions="Only an analysis, no risks section.")

    assert "carries no <code>Fix Risks</code> section" in page
    assert 'id="fix-risks"' not in page


def test_empty_fix_risks_is_reported(artifacts: dict[str, Path]):
    page = _page(artifacts, suggestions="Analysis here.\n\n## Fix Risks\n")

    assert 'id="fix-risks"' in page
    assert "opened the section but wrote no guidance" in page


def test_fix_risks_content_is_escaped(artifacts: dict[str, Path]):
    """The risks and the answer both come from a model reading an untrusted artifact."""
    page = _page(
        artifacts,
        suggestions="## Fix Risks\n\n<script>alert('x')</script>",
    )

    assert "<script>alert" not in page
    assert "&lt;script&gt;alert" in page
