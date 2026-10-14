"""
Tests for `build_matrix.py` - discovery of iFlows changed by `git diff`.

Unlike the extractor tests, validation here depends on a real Git repository:
the main function is precisely to turn the Git diff into a job matrix.
The test repositories are created in directories unique per session, because
environments with write restrictions may not allow removing temporary
directories.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest

HUB = Path(__file__).resolve().parents[1]
SCRIPTS = HUB / "scripts"

if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import build_matrix  # noqa: E402

IFLOW_XML = '<?xml version="1.0"?><ns0:IntegrationFlow xmlns:ns0="urn:x"/>'
RES = "src/main/resources"


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr}")
    return result.stdout.strip()


def _commit(repo: Path, message: str) -> str:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


# ---------------------------------------------------------------------------
# Test repository with a history
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def history(tmp_path_factory) -> dict:
    """
    Creates a Git repository with a history covering the scenarios that matter.

    Commit order (each one isolates a case):
        c1  initial artifact (at the repo root)
        c2  change in the .groovy only           -> does not touch the .iflw
        c3  change in the .iflw
        c4  change in documentation only         -> must be ignored
        c5  second artifact in another directory
        c6  script of the first artifact again   -> has_iflow_change=False
    """
    repo = tmp_path_factory / f"matrix-repo_{uuid.uuid4().hex[:8]}"
    (repo / RES / "scenarioflows" / "integrationflow").mkdir(parents=True)
    (repo / RES / "script").mkdir(parents=True)
    (repo / "metainfo.prop").write_text("v=1", encoding="utf-8")
    (repo / "README.md").write_text("# repo", encoding="utf-8")
    (repo / RES / "scenarioflows" / "integrationflow" / "FlowA.iflw").write_text(
        IFLOW_XML, encoding="utf-8"
    )
    (repo / RES / "script" / "Condition.groovy").write_text("// v1", encoding="utf-8")

    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.name", "Automated Test")
    _git(repo, "config", "user.email", "test@example.com")

    shas = {"c1": _commit(repo, "c1: initial artifact")}

    (repo / RES / "script" / "Condition.groovy").write_text("// v2", encoding="utf-8")
    shas["c2"] = _commit(repo, "c2: changes the groovy only")

    (repo / RES / "scenarioflows" / "integrationflow" / "FlowA.iflw").write_text(
        IFLOW_XML + "<!-- changed -->", encoding="utf-8"
    )
    shas["c3"] = _commit(repo, "c3: changes the iflw")

    (repo / "README.md").write_text("# updated repo", encoding="utf-8")
    (repo / "docs").mkdir(exist_ok=True)
    (repo / "docs" / "guide.md").write_text("guide", encoding="utf-8")
    shas["c4"] = _commit(repo, "c4: documentation only")

    (repo / "PackageB" / RES / "scenarioflows" / "integrationflow").mkdir(parents=True)
    (
        repo / "PackageB" / RES / "scenarioflows" / "integrationflow" / "FlowB.iflw"
    ).write_text(IFLOW_XML, encoding="utf-8")
    shas["c5"] = _commit(repo, "c5: second artifact")

    (repo / RES / "script" / "Condition.groovy").write_text("// v3", encoding="utf-8")
    shas["c6"] = _commit(repo, "c6: script of the first artifact again")

    return {"repo": repo, **shas}


def _build(history: dict, before: str, after: str = "HEAD", full: bool = False):
    return build_matrix.build_matrix(
        build_matrix.changed_files_git(before, after, history["repo"]),
        history["repo"],
        "SI_Glovia_SalesOrderCreate",
        force_full_scan=full,
    )


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("src/main/resources/script/X.groovy", False),
        ("src/main/resources/scenarioflows/integrationflow/F.iflw", False),
        ("README.md", True),
        ("docs/guide.md", True),
        ("test-plans/P/cases.json", True),
        ("reports/P/report.html", True),
        ("tests/P/test_flow.py", True),
        ("src/main/resources/mapping/M.mmap", True),
        (".github/workflows/w.yml", True),
    ],
)
def test_is_ignored(path: str, expected: bool):
    assert build_matrix.is_ignored(path) is expected


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("src/main/resources/scenarioflows/integrationflow/F.iflw", "."),
        ("PackageB/src/main/resources/scenarioflows/integrationflow/F.iflw", "PackageB"),
        ("F.iflw", "."),
    ],
)
def test_artifact_root_for(path: str, expected: str):
    assert build_matrix.artifact_root_for(path) == expected


def test_package_name_uses_repo_name_at_root():
    assert build_matrix._package_name(".", "MyRepo") == "MyRepo"


def test_package_name_uses_artifact_directory():
    assert build_matrix._package_name("PackageB", "MyRepo") == "PackageB"


# ---------------------------------------------------------------------------
# Matrix from real diffs
# ---------------------------------------------------------------------------


def test_change_in_script_only_creates_entry_without_iflow_change(history):
    matrix = _build(history, history["c1"], history["c2"])

    assert len(matrix) == 1
    assert matrix[0]["iflow"] == "FlowA"
    assert matrix[0]["path"] == "."
    assert matrix[0]["has_iflow_change"] is False
    assert matrix[0]["changed_files"] == ["src/main/resources/script/Condition.groovy"]


def test_iflow_change_sets_has_iflow_change(history):
    matrix = _build(history, history["c2"], history["c3"])

    assert len(matrix) == 1
    assert matrix[0]["iflow"] == "FlowA"
    assert matrix[0]["has_iflow_change"] is True


def test_change_in_documentation_only_creates_no_job(history):
    assert _build(history, history["c3"], history["c4"]) == []


def test_second_artifact_becomes_its_own_entry(history):
    matrix = _build(history, history["c4"], history["c5"])

    assert len(matrix) == 1
    assert matrix[0]["path"] == "PackageB"
    assert matrix[0]["iflow"] == "FlowB"


def test_broad_diff_lists_both_artifacts_in_stable_order(history):
    matrix = _build(history, history["c1"], history["c5"])

    assert [item["path"] for item in matrix] == [".", "PackageB"]


def test_full_scan_ignores_the_diff(history):
    matrix = _build(history, history["c6"], history["c6"], full=True)

    assert [item["path"] for item in matrix] == [".", "PackageB"]
    assert all(item["has_iflow_change"] for item in matrix)


def test_script_changed_after_the_iflow_does_not_regenerate(history):
    matrix = _build(history, history["c5"], history["c6"])

    assert len(matrix) == 1
    assert matrix[0]["iflow"] == "FlowA"
    assert matrix[0]["has_iflow_change"] is False


def test_changed_files_has_no_duplicates(history):
    matrix = _build(history, history["c1"], history["c5"])

    for item in matrix:
        assert len(item["changed_files"]) == len(set(item["changed_files"]))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _run_cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPTS / "build_matrix.py"), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def test_cli_writes_json_to_stdout(history):
    result = _run_cli(
        "--repo", str(history["repo"]), "--before", history["c2"], "--after", history["c3"]
    )

    assert result.returncode == 0
    matrix = json.loads(result.stdout)
    assert len(matrix) == 1
    assert matrix[0]["iflow"] == "FlowA"


def test_cli_without_changes_returns_empty_matrix(history):
    result = _run_cli(
        "--repo", str(history["repo"]), "--before", history["c3"], "--after", history["c4"]
    )

    assert result.returncode == 0
    assert json.loads(result.stdout) == []


def test_cli_zeroed_before_runs_full_scan(history):
    """First push of a branch sends `before` as 40 zeros."""
    result = _run_cli("--repo", str(history["repo"]), "--before", "0" * 40)

    assert result.returncode == 0
    assert len(json.loads(result.stdout)) == 2


def test_cli_nonexistent_sha_falls_back_to_full_scan(history):
    """Force-push or shallow clone: `before` does not exist, but it must not break."""
    result = _run_cli("--repo", str(history["repo"]), "--before", "a" * 40)

    assert result.returncode == 0
    assert len(json.loads(result.stdout)) == 2


def test_cli_writes_file_when_requested(history, tmp_path: Path):
    destination = tmp_path / "matrix.json"
    result = _run_cli(
        "--repo", str(history["repo"]),
        "--before", history["c4"], "--after", history["c5"],
        "--output", str(destination),
    )

    assert result.returncode == 0
    assert result.stdout.strip() == ""
    assert json.loads(destination.read_text(encoding="utf-8"))[0]["path"] == "PackageB"


def test_cli_nonexistent_repo_returns_exit_2(tmp_path: Path):
    result = _run_cli("--repo", str(tmp_path / "nonexistent"))

    assert result.returncode == 2
    assert "repository not found" in result.stderr
