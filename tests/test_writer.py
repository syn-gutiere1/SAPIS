"""
Tests for the artifact writer.

The focus is security: the filename comes from the model, which was influenced by
the `.iflw` - an untrusted input. The original pipeline wrote
`out_dir / item["file"]` directly, allowing writes outside the destination folder.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pipeline.writer import (
    ALLOWED_TESTDATA_SUFFIXES,
    WriteError,
    clean_output_dir,
    find_key,
    safe_relative_path,
    write_test_package,
)

BASE_PACKAGE = {
    "scenarios": [{"id": "TC-001", "name": "scenario"}],
    "test_data": [],
    "pytest_script": {"filename": "test_flow.py", "content": "def test_x():\n    assert True\n"},
    "risks": [{"risk": "r"}],
    "coverage_analysis": {"target_coverage_percent": 100},
}


# ---------------------------------------------------------------------------
# Sanitization of names - path traversal
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "malicious",
    [
        "../../../etc/passwd",
        "..\\..\\..\\Windows\\System32\\evil.xml",
        "/etc/passwd.xml",
        "C:\\Windows\\System32\\evil.xml",
        "testdata/../../../outside.xml",
    ],
)
def test_path_traversal_is_reduced_to_base_name(malicious: str):
    """
    Any traversal attempt must result in a simple name, never in a
    path. The base name is accepted - what matters is that there is no directory.
    """
    result = safe_relative_path(malicious)

    assert "/" not in result
    assert "\\" not in result
    assert ".." not in result
    assert result in ("passwd", "passwd.xml", "evil.xml", "outside.xml")


@pytest.mark.parametrize("empty", ["", "   ", ".", "..", "/", "\\", "./"])
def test_invalid_names_are_rejected(empty: str):
    with pytest.raises(WriteError):
        safe_relative_path(empty)


def test_non_text_name_is_rejected():
    with pytest.raises(WriteError, match="text expected"):
        safe_relative_path(None)  # type: ignore[arg-type]


@pytest.mark.parametrize("reserved", ["test_flow.py", "cases.json", "risks.json"])
def test_name_reserved_for_pipeline_is_rejected(reserved: str):
    """A fixture from the model cannot overwrite an artifact of the pipeline."""
    with pytest.raises(WriteError, match="Name reserved for the pipeline"):
        safe_relative_path(reserved)


@pytest.mark.parametrize("device", ["CON", "nul.json", "aux.xml", "LPT1.xml"])
def test_device_name_is_rejected(device: str):
    with pytest.raises(WriteError, match="reserved by the system"):
        safe_relative_path(device)


def test_extension_outside_whitelist_is_rejected():
    with pytest.raises(WriteError, match="Extension not allowed"):
        safe_relative_path("payload.py", allowed_suffixes=ALLOWED_TESTDATA_SUFFIXES)


def test_allowed_extension_passes():
    assert safe_relative_path("payload.xml", allowed_suffixes=ALLOWED_TESTDATA_SUFFIXES) == "payload.xml"


def test_name_with_space_and_parentheses_is_preserved():
    """Legitimate SAP names can have spaces and parentheses."""
    assert safe_relative_path("Log_Payload (1).xml") == "Log_Payload (1).xml"


# ---------------------------------------------------------------------------
# Writing the package
# ---------------------------------------------------------------------------


def test_writes_all_artifacts(tmp_path: Path):
    summary = write_test_package(BASE_PACKAGE, tmp_path)

    assert (tmp_path / "cases.json").is_file()
    assert (tmp_path / "risks.json").is_file()
    assert (tmp_path / "coverage.json").is_file()
    assert (tmp_path / "test_flow.py").is_file()
    assert (tmp_path / "raw_llm_output.json").is_file()
    assert (tmp_path / "testdata").is_dir()
    assert summary["scenarios"] == 1
    assert summary["risks"] == 1
    assert summary["rejected"] == []


def test_writes_fixtures_into_testdata(tmp_path: Path):
    package = dict(BASE_PACKAGE)
    package["test_data"] = [
        {"filename": "testdata/valid.xml", "content": "<a/>"},
        {"filename": "invalid.json", "content": "{}"},
    ]

    summary = write_test_package(package, tmp_path)

    assert (tmp_path / "testdata" / "valid.xml").read_text(encoding="utf-8") == "<a/>"
    assert (tmp_path / "testdata" / "invalid.json").is_file()
    assert summary["testdata_files"] == 2


def test_path_traversal_does_not_escape_destination(tmp_path: Path):
    """The test that matters: the file has to land inside testdata/."""
    destination = tmp_path / "output"
    package = dict(BASE_PACKAGE)
    package["test_data"] = [{"filename": "../../outside.xml", "content": "<x/>"}]

    write_test_package(package, destination)

    assert (destination / "testdata" / "outside.xml").is_file()
    assert not (tmp_path / "outside.xml").exists()
    assert not (tmp_path.parent / "outside.xml").exists()


def test_fixture_with_forbidden_extension_is_discarded(tmp_path: Path):
    package = dict(BASE_PACKAGE)
    package["test_data"] = [{"filename": "payload.py", "content": "print(1)"}]

    summary = write_test_package(package, tmp_path)

    assert summary["testdata_files"] == 0
    assert len(summary["rejected"]) == 1
    assert not (tmp_path / "testdata" / "payload.py").exists()


def test_fixture_without_content_is_discarded(tmp_path: Path):
    package = dict(BASE_PACKAGE)
    package["test_data"] = [{"filename": "x.xml"}]

    summary = write_test_package(package, tmp_path)

    assert summary["testdata_files"] == 0
    assert len(summary["rejected"]) == 1


def test_duplicate_fixture_is_discarded(tmp_path: Path):
    """Two declarations of the same name mean content silently lost."""
    package = dict(BASE_PACKAGE)
    package["test_data"] = [
        {"filename": "x.xml", "content": "<first/>"},
        {"filename": "x.xml", "content": "<second/>"},
    ]

    summary = write_test_package(package, tmp_path)

    assert summary["testdata_files"] == 1
    assert (tmp_path / "testdata" / "x.xml").read_text(encoding="utf-8") == "<first/>"
    assert any("declared more than once" in item for item in summary["rejected"])


def test_cleans_leftover_from_previous_run(tmp_path: Path):
    """
    Regression of a real problem: the `testdata/` of Glovia had 41 files while
    the current run declared 13. The folder must be cleaned on every run.
    """
    testdata = tmp_path / "testdata"
    testdata.mkdir(parents=True)
    (testdata / "old.xml").write_text("<old/>", encoding="utf-8")
    (tmp_path / "test_flow.py").write_text("# old suite", encoding="utf-8")

    package = dict(BASE_PACKAGE)
    package["test_data"] = [{"filename": "new.xml", "content": "<new/>"}]
    write_test_package(package, tmp_path)

    assert not (testdata / "old.xml").exists()
    assert (testdata / "new.xml").is_file()
    assert "# old suite" not in (tmp_path / "test_flow.py").read_text(encoding="utf-8")


def test_clean_false_preserves_existing_files(tmp_path: Path):
    testdata = tmp_path / "testdata"
    testdata.mkdir(parents=True)
    (testdata / "old.xml").write_text("<old/>", encoding="utf-8")

    write_test_package(BASE_PACKAGE, tmp_path, clean=False)

    assert (testdata / "old.xml").is_file()


def test_clean_output_dir_on_missing_folder_does_not_fail(tmp_path: Path):
    clean_output_dir(tmp_path / "does_not_exist")


# ---------------------------------------------------------------------------
# Flexible reading of the response
# ---------------------------------------------------------------------------


def test_find_key_accepts_name_variations():
    assert find_key({"Scenarios": [1]}, ["scenario"]) == [1]
    assert find_key({"test data": [2]}, ["test_data", "test data"]) == [2]
    assert find_key({"other": 1}, ["scenario"]) is None


def test_test_data_as_single_object_is_normalized(tmp_path: Path):
    package = dict(BASE_PACKAGE)
    package["test_data"] = {"filename": "single.xml", "content": "<u/>"}

    summary = write_test_package(package, tmp_path)

    assert summary["testdata_files"] == 1
    assert (tmp_path / "testdata" / "single.xml").is_file()


def test_pytest_script_as_list_is_concatenated(tmp_path: Path):
    package = dict(BASE_PACKAGE)
    package["pytest_script"] = ["def test_a():", "    assert True"]

    write_test_package(package, tmp_path)

    content = (tmp_path / "test_flow.py").read_text(encoding="utf-8")
    assert "def test_a():" in content
    assert "assert True" in content


def test_without_pytest_script_does_not_create_test_flow(tmp_path: Path):
    package = dict(BASE_PACKAGE)
    package["pytest_script"] = ""

    write_test_package(package, tmp_path)

    assert not (tmp_path / "test_flow.py").exists()


def test_raw_llm_output_preserves_the_response(tmp_path: Path):
    write_test_package(BASE_PACKAGE, tmp_path)

    saved = json.loads((tmp_path / "raw_llm_output.json").read_text(encoding="utf-8"))
    assert saved["scenarios"] == BASE_PACKAGE["scenarios"]
