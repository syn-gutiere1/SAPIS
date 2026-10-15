"""
Tests for the directory extractor (`extract_from_dir`).

Covers blocker B1': the original pipeline only knew how to read `.zip`, but the
native `Git Push` of SAP IS writes the files exploded.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from pipeline.extract import (
    ExtractionError,
    artifact_root_for,
    collect_scripts,
    extract,
    extract_from_dir,
    extract_from_zip,
    find_iflow_files,
)

# ---------------------------------------------------------------------------
# Fixtures: a synthetic package in the same format as SAP IS
# ---------------------------------------------------------------------------

IFLOW_XML = """<?xml version="1.0" encoding="UTF-8"?>
<ns0:IntegrationFlow xmlns:ns0="http://sap.com/xi/XI/SplitAndMerge">
  <integrationFlow name="TestFlow"/>
</ns0:IntegrationFlow>
"""


@pytest.fixture
def sap_package(tmp_path: Path) -> Path:
    """Reproduces the layout observed in a package exported from SAP IS."""
    root = tmp_path / "TestPackage"
    resources = root / "src" / "main" / "resources"
    (resources / "scenarioflows" / "integrationflow").mkdir(parents=True)
    (resources / "script").mkdir(parents=True)
    (resources / "mapping").mkdir(parents=True)
    (root / "META-INF").mkdir(parents=True)

    (root / ".project").write_text("<projectDescription/>", encoding="utf-8")
    (root / "metainfo.prop").write_text("version=1\n", encoding="utf-8")
    (root / "META-INF" / "MANIFEST.MF").write_text("Manifest-Version: 1.0\n", encoding="utf-8")
    (resources / "scenarioflows" / "integrationflow" / "TestFlow.iflw").write_text(
        IFLOW_XML, encoding="utf-8"
    )
    (resources / "script" / "Condition.groovy").write_text(
        "def Message processData(Message m) { m }", encoding="utf-8"
    )
    (resources / "script" / "Log_Payload.gsh").write_text("// log", encoding="utf-8")
    (resources / "mapping" / "Map.mmap").write_text("<map/>", encoding="utf-8")
    return root


# ---------------------------------------------------------------------------
# File discovery
# ---------------------------------------------------------------------------


def test_find_iflow_files_finds_the_iflw(sap_package: Path):
    found = find_iflow_files(sap_package)
    assert len(found) == 1
    assert found[0].name == "TestFlow.iflw"


def test_find_iflow_files_empty_directory_returns_empty_list(tmp_path: Path):
    assert find_iflow_files(tmp_path) == []


def test_find_iflow_files_missing_path_raises_error(tmp_path: Path):
    with pytest.raises(ExtractionError, match="Directory not found"):
        find_iflow_files(tmp_path / "missing")


def test_find_iflow_files_ignores_automation_directories(sap_package: Path):
    """`tests/`, `reports/` and the like must not be mistaken for the artifact."""
    for name in ("tests", "reports", "test-plans"):
        target = sap_package / name / "integrationflow"
        target.mkdir(parents=True)
        (target / "Fake.iflw").write_text(IFLOW_XML, encoding="utf-8")

    found = find_iflow_files(sap_package)
    assert [p.name for p in found] == ["TestFlow.iflw"]


def test_root_with_name_similar_to_excluded_directory_does_not_exclude_itself(tmp_path: Path):
    """
    Regression: exclusion by name must look at the relative path, never at the
    absolute one. A root containing 'tests' in its name cannot zero the extraction.
    """
    root = tmp_path / "tests" / "MyFlow"
    target = root / "src" / "main" / "resources" / "scenarioflows" / "integrationflow"
    target.mkdir(parents=True)
    (target / "MyFlow.iflw").write_text(IFLOW_XML, encoding="utf-8")

    assert len(find_iflow_files(root)) == 1


# ---------------------------------------------------------------------------
# Artifact root
# ---------------------------------------------------------------------------


def test_artifact_root_walks_up_to_the_package(sap_package: Path):
    iflow = find_iflow_files(sap_package)[0]
    assert artifact_root_for(iflow) == sap_package


def test_artifact_root_flat_layout(tmp_path: Path):
    flat = tmp_path / "FlatFlow"
    flat.mkdir()
    iflow = flat / "FlatFlow.iflw"
    iflow.write_text(IFLOW_XML, encoding="utf-8")
    assert artifact_root_for(iflow) == flat


# ---------------------------------------------------------------------------
# Script collection
# ---------------------------------------------------------------------------


def test_collect_scripts_takes_only_script_extensions(sap_package: Path):
    scripts = collect_scripts(sap_package)
    assert set(scripts) == {
        "src/main/resources/script/Condition.groovy",
        "src/main/resources/script/Log_Payload.gsh",
    }
    assert "Map.mmap" not in " ".join(scripts)


def test_collect_scripts_keeps_an_awkward_name(tmp_path: Path):
    """
    SAP can hand us a script whose name carries spaces, punctuation or parentheses -
    a duplicate download lands as `My Script (1).gsh`. The collector reads the name
    verbatim; only the harness sanitises it, and only to name a fixture folder.
    """
    script_dir = tmp_path / "src" / "main" / "resources" / "script"
    script_dir.mkdir(parents=True)
    (script_dir / "My Script (1).gsh").write_text("// log", encoding="utf-8")
    (script_dir / "Plain.groovy").write_text("// plain", encoding="utf-8")

    scripts = collect_scripts(tmp_path)

    assert "src/main/resources/script/My Script (1).gsh" in scripts
    assert "src/main/resources/script/Plain.groovy" in scripts


# ---------------------------------------------------------------------------
# extract_from_dir
# ---------------------------------------------------------------------------


def test_extract_from_dir_returns_xml_scripts_and_file(sap_package: Path):
    xml, scripts, iflow = extract_from_dir(sap_package)

    assert "IntegrationFlow" in xml
    assert iflow.name == "TestFlow.iflw"
    assert set(scripts) == {"script/Condition.groovy", "script/Log_Payload.gsh"}
    assert "processData" in scripts["script/Condition.groovy"]


def test_extract_from_dir_keys_without_package_prefix(sap_package: Path):
    """The paths handed to the LLM must be short and readable."""
    _xml, scripts, _iflow = extract_from_dir(sap_package)
    assert all(not name.startswith("src/main/resources/") for name in scripts)


def test_extract_from_dir_without_script_still_returns_xml(sap_package: Path):
    for script_file in (sap_package / "src" / "main" / "resources" / "script").iterdir():
        script_file.unlink()

    xml, scripts, _iflow = extract_from_dir(sap_package)
    assert "IntegrationFlow" in xml
    assert scripts == {}


def test_extract_from_dir_directory_without_iflow_raises_error(tmp_path: Path):
    with pytest.raises(ExtractionError, match="No .iflw file found"):
        extract_from_dir(tmp_path)


# ---------------------------------------------------------------------------
# extract() - dispatch by source type
# ---------------------------------------------------------------------------


def test_extract_dispatches_directory(sap_package: Path):
    _xml, scripts, iflow = extract(sap_package)
    assert iflow is not None
    assert scripts


def test_extract_dispatches_iflw_file(sap_package: Path):
    iflow_path = find_iflow_files(sap_package)[0]
    xml, scripts, iflow = extract(iflow_path)
    assert "IntegrationFlow" in xml
    assert iflow == iflow_path
    # The keys must be identical to those of the directory path - including the
    # `script/` subdirectory, which carries useful information for the LLM.
    assert set(scripts) == {"script/Condition.groovy", "script/Log_Payload.gsh"}


def test_extract_invalid_source(tmp_path: Path):
    with pytest.raises(ExtractionError, match="Invalid source"):
        extract(tmp_path / "file.xyz")


# ---------------------------------------------------------------------------
# .zip path (legacy, local use)
# ---------------------------------------------------------------------------


def test_extract_from_zip_kept_for_local_use(sap_package: Path, tmp_path: Path):
    zip_package = tmp_path / "package.zip"
    with zipfile.ZipFile(zip_package, "w") as archive:
        for artifact_file in sap_package.rglob("*"):
            if artifact_file.is_file():
                archive.write(artifact_file, artifact_file.relative_to(sap_package).as_posix())

    xml, scripts = extract_from_zip(zip_package)
    assert "IntegrationFlow" in xml
    assert any("Condition.groovy" in name for name in scripts)


def test_extract_from_zip_without_iflow_raises_error(tmp_path: Path):
    package = tmp_path / "empty.zip"
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr("readme.txt", "nothing here")

    with pytest.raises(ExtractionError, match="No IntegrationFlow XML found"):
        extract_from_zip(package)


# ---------------------------------------------------------------------------
# Validation against the REAL artifact (when present in the environment)
# ---------------------------------------------------------------------------

REAL_ARTIFACT = (
    Path(__file__).resolve().parents[3]
    / "export"
    / "Order"
    / "SI_Glovia_SalesOrderCreate"
)


@pytest.mark.skipif(
    not REAL_ARTIFACT.is_dir(),
    reason="real artifact from export/Order not available in this environment",
)
def test_extractor_against_the_real_glovia_package():
    """
    Pins the extractor against the layout SAP actually wrote, instead of only against a
    fixture built by us.

    This directory is the ORIGINAL download, frozen: it is not the copy under
    `CI-CD/iflows/`, which has since been edited (the condition script was fixed and the
    payload logger renamed). So the assertion is on the shape of what SAP exports, not
    on the current file names - pinning those here would fail every time the artifact is
    legitimately modified.
    """
    xml, scripts, iflow = extract_from_dir(REAL_ARTIFACT)

    assert iflow.name == "SI_Glovia_SalesOrderCreate.iflw"
    assert len(xml) > 40_000

    # SAP wrote three scripts. Names are deliberately not pinned; the layout is.
    assert len(scripts) >= 3
    assert all(name.startswith("script/") for name in scripts)
    assert any(name.endswith(".groovy") for name in scripts)
    assert any(name.endswith(".gsh") for name in scripts)
