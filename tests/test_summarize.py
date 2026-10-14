"""
Tests for the Job Summary generator.

The summary is the only part of the pipeline the team reads without downloading
anything, so it has to be robust to missing or partial inputs: a run that fails
before generating the manifest must still produce a useful summary.
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

import summarize  # noqa: E402

JUNIT_OK = """<?xml version="1.0" encoding="utf-8"?>
<testsuites>
  <testsuite name="pytest" errors="0" failures="0" skipped="1" tests="3" time="0.10">
    <testcase classname="test_flow.TestA" name="test_one" time="0.01"/>
    <testcase classname="test_flow.TestA" name="test_two" time="0.01"/>
    <testcase classname="test_flow.TestB" name="test_three" time="0.00">
      <skipped message="missing fixture"/>
    </testcase>
  </testsuite>
</testsuites>
"""

JUNIT_FAILURE = """<?xml version="1.0" encoding="utf-8"?>
<testsuites>
  <testsuite name="pytest" errors="1" failures="1" skipped="0" tests="3" time="0.50">
    <testcase classname="test_flow.TestA" name="test_ok" time="0.01"/>
    <testcase classname="test_flow.TestB" name="test_failure" time="0.02">
      <failure message="assert False">E   AssertionError: expected true
E   assert False</failure>
    </testcase>
    <testcase classname="test_flow.TestC" name="test_error" time="0.00">
      <error message="MissingPropertyException">E   No such property: messageLogFactory</error>
    </testcase>
  </testsuite>
</testsuites>
"""

MANIFEST = {
    "iflow": "FlowTeste",
    "iflow_sha256": "b" * 64,
    "scripts_count": 2,
    "attempts": 2,
    "truncated": [],
    "artifacts": {
        "scenarios": 30,
        "risks": 10,
        "testdata_files": 13,
        "rejected": [],
    },
    "llm": {"model": "gemini-x", "total_tokens": 15_000},
    "prompt": {"sha256": "c" * 64},
    "environment": {"github_sha": "a" * 40},
}


@pytest.fixture
def junit_ok(tmp_path: Path) -> Path:
    path = tmp_path / "junit.xml"
    path.write_text(JUNIT_OK, encoding="utf-8")
    return path


@pytest.fixture
def junit_failure(tmp_path: Path) -> Path:
    path = tmp_path / "junit.xml"
    path.write_text(JUNIT_FAILURE, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Junit parsing
# ---------------------------------------------------------------------------


def test_parse_junit_totals(junit_ok: Path):
    result = summarize.parse_junit(junit_ok)

    assert result["tests"] == 3
    assert result["failures"] == 0
    assert result["errors"] == 0
    assert result["skipped"] == 1
    assert result["time"] == pytest.approx(0.10)


def test_parse_junit_classifies_each_case(junit_failure: Path):
    cases = {c["name"]: c["status"] for c in summarize.parse_junit(junit_failure)["cases"]}

    assert cases == {"test_ok": "passed", "test_failure": "failed", "test_error": "error"}


def test_parse_junit_captures_message_and_detail(junit_failure: Path):
    case = next(
        c for c in summarize.parse_junit(junit_failure)["cases"] if c["name"] == "test_failure"
    )

    assert "assert False" in case["message"]
    assert "AssertionError" in case["detail"]


def test_parse_junit_missing_file(tmp_path: Path):
    assert summarize.parse_junit(tmp_path / "does_not_exist.xml") is None


def test_parse_junit_invalid_xml(tmp_path: Path):
    broken = tmp_path / "broken.xml"
    broken.write_text("<testsuites><testsuite", encoding="utf-8")

    assert summarize.parse_junit(broken) is None


def test_parse_junit_accepts_single_testsuite(tmp_path: Path):
    path = tmp_path / "single.xml"
    path.write_text(
        '<?xml version="1.0"?><testsuite name="p" tests="1" failures="0" errors="0" '
        'skipped="0" time="0.01"><testcase classname="c" name="t"/></testsuite>',
        encoding="utf-8",
    )

    assert summarize.parse_junit(path)["tests"] == 1


# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------


def test_render_shows_success_verdict(junit_ok: Path):
    text = summarize.render(
        manifest=None, junit=summarize.parse_junit(junit_ok), logs=None, iflow="FlowX", artifact_name=None
    )

    assert "🟢" in text
    assert "FlowX" in text
    assert "| 3 | 2 | 0 | 0 | 1 |" in text


def test_render_shows_failure_verdict(junit_failure: Path):
    text = summarize.render(
        manifest=MANIFEST,
        junit=summarize.parse_junit(junit_failure),
        logs=None,
        iflow=None,
        artifact_name="results-FlowTeste",
    )

    assert "🔴" in text
    assert "### Failures (2)" in text
    assert "test_flow.TestB::test_failure" in text
    assert "<details>" in text


def test_render_includes_provenance(junit_ok: Path):
    text = summarize.render(
        manifest=MANIFEST, junit=summarize.parse_junit(junit_ok), logs=None, iflow=None, artifact_name=None
    )

    assert "### Provenance" in text
    assert "gemini-x" in text
    assert "15000" in text
    assert "aaaaaaaaaaaa" in text  # start of github_sha
    assert "cccccccccccc" in text  # start of prompt_sha256


def test_render_warns_about_truncation(junit_ok: Path):
    manifest = dict(MANIFEST)
    manifest["truncated"] = ["flow_xml (45000 -> 30000 chars)"]

    text = summarize.render(
        manifest=manifest, junit=summarize.parse_junit(junit_ok), logs=None, iflow=None, artifact_name=None
    )

    assert "Content truncated before submission" in text
    assert "flow_xml" in text


def test_render_lists_discarded_fixtures(junit_ok: Path):
    manifest = json.loads(json.dumps(MANIFEST))
    manifest["artifacts"]["rejected"] = ["'payload.py': Extension not allowed for fixture"]

    text = summarize.render(
        manifest=manifest, junit=summarize.parse_junit(junit_ok), logs=None, iflow=None, artifact_name=None
    )

    assert "### Discarded fixtures" in text
    assert "payload.py" in text


def test_render_without_manifest_still_generates_summary(junit_ok: Path):
    """A run that fails before generating the manifest still needs a summary."""
    text = summarize.render(
        manifest=None, junit=summarize.parse_junit(junit_ok), logs=None, iflow="FlowX", artifact_name=None
    )

    assert "Result" in text
    assert "Provenance" not in text


def test_render_without_manifest_explains_skipped_generation(junit_ok: Path):
    """
    Happens when the `.iflw` did not change: the generation is skipped on
    purpose, and the summary must say so instead of making it look like the
    provenance was lost.
    """
    text = summarize.render(
        manifest=None, junit=summarize.parse_junit(junit_ok), logs=None, iflow="FlowX", artifact_name=None
    )

    assert "the generation did not run" in text
    assert "tests already versioned" in text


def test_render_manifest_with_null_fields(junit_ok: Path):
    """A run manifest without generation carries null fields; it cannot show None."""
    manifest = dict(MANIFEST)
    manifest["llm"] = {"model": None, "total_tokens": None}
    manifest["artifacts"] = {"scenarios": None, "risks": None, "testdata_files": None}
    manifest["prompt"] = {"sha256": None}
    manifest["iflow_sha256"] = None

    text = summarize.render(
        manifest=manifest, junit=summarize.parse_junit(junit_ok), logs=None, iflow=None, artifact_name=None
    )

    assert "None" not in text.replace("None_", "")
    assert "| Model | `-` |" in text


def test_render_without_junit_uses_log(tmp_path: Path, junit_ok: Path):
    log = tmp_path / "pytest_logs.txt"
    log.write_text("irrelevant line\n1 failed, 2 passed in 0.5s\n", encoding="utf-8")

    text = summarize.render(manifest=None, junit=None, logs=log, iflow="FlowX", artifact_name=None)

    assert "### End of the pytest log" in text
    assert "1 failed, 2 passed" in text


def test_render_without_junit_or_log_warns(tmp_path: Path):
    text = summarize.render(
        manifest=MANIFEST, junit=None, logs=tmp_path / "missing.txt", iflow=None, artifact_name=None
    )

    assert "Test result not available" in text


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPTS / "summarize.py"), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def test_cli_requires_some_input():
    result = _run()

    assert result.returncode == 2
    assert "[ERROR] Provide at least one of --manifest, --junit or --logs." in result.stderr


def test_cli_generates_markdown(junit_failure: Path, tmp_path: Path):
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(MANIFEST), encoding="utf-8")

    result = _run(
        "--manifest", str(manifest_path),
        "--junit", str(junit_failure),
        "--artifact-name", "results-FlowTeste",
    )

    assert result.returncode == 0
    assert "## Unified test plan" in result.stdout
    assert "results-FlowTeste" in result.stdout


def test_cli_invalid_manifest_does_not_break(junit_failure: Path, tmp_path: Path):
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text("{ not json", encoding="utf-8")

    result = _run("--manifest", str(manifest_path), "--junit", str(junit_failure))

    assert result.returncode == 0
    assert "Warning: invalid" in result.stderr
    assert "## Unified test plan" in result.stdout
