"""
Tests for the Groovy harness.

The harness runs the REAL `.groovy` scripts of an iFlow. The previous pipeline
never did that: the `.bat` only compiled the stubs, and the generated tests
*simulated* the rule in Python. That gap is exactly what hid a real defect - the
Glovia condition script never extracted the order code (see
`test_harness_detects_script_with_namespace_bug`).

Note on the stubs: there is no `groovyc` step. Putting the stub SOURCE on the
classpath is enough, because Groovy compiles on demand. This also avoids a
Windows trap: `groovyc` is a `.bat`, and `CreateProcess` cannot execute batch
files directly, so `subprocess` fails with FileNotFoundError.

These tests need `groovy` on PATH. When it is missing - or when the environment
forbids spawning subprocesses - they are skipped instead of failing.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
HARNESS = REPO / "groovy-harness" / "TestHarness.groovy"
STUBS = REPO / "stubs"

GROOVY_AVAILABLE = shutil.which("groovy") is not None

pytestmark = pytest.mark.skipif(
    not GROOVY_AVAILABLE, reason="groovy is not installed in this environment"
)

WORKING_SCRIPT = """
import com.sap.gateway.ip.core.customdev.util.Message

def Message processData(Message message) {
    def body = message.getBody(String)
    def xml = new XmlSlurper().parseText(body)
    def orderCode = xml.Body.notifications.Notification.sObject.giic_OrderTypeCode__c.text()
    def validCodes = ['YORB', 'YICB', 'POR', 'MNZ', 'PLM']
    message.setHeader("OrderCodeValid", validCodes.contains(orderCode) ? "true" : "false")
    return message
}
"""

# The real Glovia script, kept here as a regression case: it uses namespace
# prefixed accessors, which XmlSlurper discards.
SCRIPT_WITH_NAMESPACE_BUG = """
import com.sap.gateway.ip.core.customdev.util.Message

def Message processData(Message message) {
    def body = message.getBody(String)
    def xml = new XmlSlurper().parseText(body)
    def orderCode = xml.'ns1:notifications'.'ns1:Notification'.'ns1:sObject'.'ns2:giic_OrderTypeCode__c'.text()
    def validCodes = ['YORB', 'YICB', 'POR', 'MNZ', 'PLM']
    message.setHeader("OrderCodeValid", validCodes.contains(orderCode) ? "true" : "false")
    return message
}
"""

# Uses the log factory the tenant runtime injects into the script binding.
SCRIPT_WITH_LOGGING = """
import com.sap.gateway.ip.core.customdev.util.Message

def Message processData(Message message) {
    def log = messageLogFactory.getMessageLog(message)
    def country = message.getHeaders().get("Country")
    if (country != null) {
        log.addCustomHeaderProperty("Country", country)
    }
    return message
}
"""

FAILING_SCRIPT = """
import com.sap.gateway.ip.core.customdev.util.Message

def Message processData(Message message) {
    throw new IllegalStateException("deliberate failure for the test")
}
"""

PAYLOAD_YORB = """<?xml version="1.0" encoding="UTF-8"?>
<SOAP:Envelope xmlns:SOAP="http://schemas.xmlsoap.org/soap/envelope/"
               xmlns:ns1="http://soap.sforce.com/2005/09/outbound"
               xmlns:ns2="urn:sobject.enterprise.soap.sforce.com">
  <SOAP:Body>
    <ns1:notifications>
      <ns1:Notification>
        <ns1:sObject>
          <ns2:giic_OrderTypeCode__c>YORB</ns2:giic_OrderTypeCode__c>
        </ns1:sObject>
      </ns1:Notification>
    </ns1:notifications>
  </SOAP:Body>
</SOAP:Envelope>
"""


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def scenario(tmp_path: Path):
    """Build a working directory with a script and its fixture folders."""

    def _build(script: str, *, fixtures: dict[str, str], asserts: dict[str, str] | None = None):
        script_file = tmp_path / "Rule.groovy"
        script_file.write_text(script, encoding="utf-8")

        testdata = tmp_path / "testdata"
        testdata.mkdir(exist_ok=True)
        for name, content in fixtures.items():
            (testdata / name).write_text(content, encoding="utf-8")
        for name, content in (asserts or {}).items():
            (testdata / name).write_text(content, encoding="utf-8")

        return script_file, testdata

    return _build


def _groovy_command() -> list[str]:
    """
    Build the platform-correct `groovy` invocation.

    On Windows the executable is a `.bat`, and `CreateProcess` cannot run batch
    files directly - `shutil.which` finds the file but the spawn fails with
    FileNotFoundError. Going through `cmd /c` fixes it. On Linux the binary is a
    real executable and no prefix is needed.
    """
    if sys.platform.startswith("win"):
        return ["cmd", "/c", "groovy"]
    return ["groovy"]


def _run_single(script_file: Path, testdata: Path, *extra: str):
    return subprocess.run(
        [
            *_groovy_command(),
            "-cp", str(STUBS),
            str(HARNESS),
            "--script", str(script_file),
            "--testdata", str(testdata),
            *extra,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def _run_scan(scripts: Path, testdata: Path, *extra: str):
    return subprocess.run(
        [
            *_groovy_command(),
            "-cp", str(STUBS),
            str(HARNESS),
            "--scan-all", str(scripts),
            "--testdata", str(testdata),
            *extra,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


# ---------------------------------------------------------------------------
# Running real scripts
# ---------------------------------------------------------------------------


def test_valid_script_passes(scenario):
    script, testdata = scenario(
        WORKING_SCRIPT,
        fixtures={"yorb.xml": PAYLOAD_YORB},
        asserts={"yorb.assert.json": '{"headers": {"OrderCodeValid": "true"}}'},
    )

    result = _run_single(script, testdata)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "[PASS]" in result.stdout
    assert "1 passed, 0 failed" in result.stdout


def test_wrong_expectation_fails_the_test(scenario):
    """Guarantees the harness actually verifies, instead of always passing."""
    script, testdata = scenario(
        WORKING_SCRIPT,
        fixtures={"yorb.xml": PAYLOAD_YORB},
        asserts={"yorb.assert.json": '{"headers": {"OrderCodeValid": "false"}}'},
    )

    result = _run_single(script, testdata)

    assert result.returncode == 1
    assert "[FAIL]" in result.stdout
    assert "expected 'false', got 'true'" in result.stdout


def test_harness_detects_script_with_namespace_bug(scenario):
    """
    Regression for the real defect found in the Glovia iFlow.

    `GLOVIA_Conditioncheck_Salesorder_Create.groovy` uses prefixed accessors
    (`xml.'ns1:notifications'`). XmlSlurper discards namespace prefixes, so those
    accessors return nothing and the script marks EVERY order as invalid. The
    Python tests never caught it because they simulated the rule instead of
    running the script.
    """
    script, testdata = scenario(
        SCRIPT_WITH_NAMESPACE_BUG,
        fixtures={"yorb.xml": PAYLOAD_YORB},
        asserts={"yorb.assert.json": '{"headers": {"OrderCodeValid": "true"}}'},
    )

    result = _run_single(script, testdata)

    assert result.returncode == 1, "the harness should fail the script with the bug"
    assert "got 'false'" in result.stdout


def test_exception_in_script_fails_the_test(scenario):
    script, testdata = scenario(FAILING_SCRIPT, fixtures={"yorb.xml": PAYLOAD_YORB})

    result = _run_single(script, testdata)

    assert result.returncode == 1
    assert "IllegalStateException" in result.stdout


def test_script_without_process_data_fails(scenario):
    script, testdata = scenario("def somethingElse() { 1 }", fixtures={"yorb.xml": PAYLOAD_YORB})

    result = _run_single(script, testdata)

    assert result.returncode == 1
    assert "processData" in result.stdout


# ---------------------------------------------------------------------------
# Tenant runtime injection
# ---------------------------------------------------------------------------


def test_message_log_factory_is_injected(scenario):
    """
    Real scripts call `messageLogFactory.getMessageLog(message)`. Without the
    binding injection they fail with MissingPropertyException.
    """
    script, testdata = scenario(
        SCRIPT_WITH_LOGGING,
        fixtures={"yorb.xml": PAYLOAD_YORB},
        asserts={
            "yorb.inject.json": '{"headers": {"Country": "US"}}',
            "yorb.assert.json": '{"log_headers": {"Country": "US"}}',
        },
    )

    result = _run_single(script, testdata)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "[PASS]" in result.stdout


def test_get_body_with_class_argument_works(scenario):
    """
    `message.getBody(String)` is used by real scripts. The original stub did not
    declare `getBody(Class)` and the harness failed with MissingMethodException.
    """
    script, testdata = scenario(WORKING_SCRIPT, fixtures={"yorb.xml": PAYLOAD_YORB})

    result = _run_single(script, testdata)

    assert "MissingMethodException" not in result.stdout + result.stderr


def test_injected_headers_reach_the_script(scenario):
    script = """
import com.sap.gateway.ip.core.customdev.util.Message

def Message processData(Message message) {
    message.setHeader("Echo", message.getHeader("Input"))
    return message
}
"""
    script_file, testdata = scenario(
        script,
        fixtures={"yorb.xml": PAYLOAD_YORB},
        asserts={
            "yorb.inject.json": '{"headers": {"Input": "injected-value"}}',
            "yorb.assert.json": '{"headers": {"Echo": "injected-value"}}',
        },
    )

    result = _run_single(script_file, testdata)

    assert result.returncode == 0, result.stdout


# ---------------------------------------------------------------------------
# JUnit XML
# ---------------------------------------------------------------------------


def test_junit_xml_is_generated_and_valid(scenario, tmp_path: Path):
    script, testdata = scenario(
        WORKING_SCRIPT,
        fixtures={"yorb.xml": PAYLOAD_YORB, "another.xml": PAYLOAD_YORB},
    )
    junit = tmp_path / "junit-groovy.xml"

    result = _run_single(script, testdata, "--junit", str(junit))

    assert result.returncode == 0
    assert junit.is_file()

    root = ET.parse(junit).getroot()
    assert root.get("tests") == "2"
    assert root.get("failures") == "0"
    assert len(list(root.iter("testcase"))) == 2


def test_junit_records_failure(scenario, tmp_path: Path):
    script, testdata = scenario(
        WORKING_SCRIPT,
        fixtures={"yorb.xml": PAYLOAD_YORB},
        asserts={"yorb.assert.json": '{"headers": {"OrderCodeValid": "false"}}'},
    )
    junit = tmp_path / "junit-groovy.xml"

    _run_single(script, testdata, "--junit", str(junit))

    root = ET.parse(junit).getroot()
    assert root.get("failures") == "1"
    failure = next(root.iter("failure"))
    assert "OrderCodeValid" in (failure.get("message") or "")


def test_junit_escapes_special_characters(scenario, tmp_path: Path):
    """
    The fixture name comes from the repository, so there is no implicit trust.

    An `&` in the filename would break the XML if it were not escaped, and
    dorny/test-reporter would fail to read the report.
    """
    script, testdata = scenario(WORKING_SCRIPT, fixtures={"normal.xml": PAYLOAD_YORB})
    (testdata / "normal.xml").rename(testdata / "a&b.xml")

    junit = tmp_path / "junit-groovy.xml"
    result = _run_single(script, testdata, "--junit", str(junit))

    root = ET.parse(junit).getroot()
    assert root.get("tests") == "1"
    assert any("a&b" in (c.get("name") or "") for c in root.iter("testcase"))


# ---------------------------------------------------------------------------
# Bad usage
# ---------------------------------------------------------------------------


def test_missing_script_returns_exit_2(scenario, tmp_path: Path):
    _script, testdata = scenario(WORKING_SCRIPT, fixtures={"yorb.xml": PAYLOAD_YORB})

    result = _run_single(tmp_path / "does_not_exist.groovy", testdata)

    assert result.returncode == 2


def test_missing_testdata_returns_exit_2(scenario, tmp_path: Path):
    script, _testdata = scenario(WORKING_SCRIPT, fixtures={"yorb.xml": PAYLOAD_YORB})

    result = _run_single(script, tmp_path / "no_testdata")

    assert result.returncode == 2


def test_filter_selects_a_single_fixture(scenario):
    script, testdata = scenario(
        WORKING_SCRIPT, fixtures={"yorb.xml": PAYLOAD_YORB, "another.xml": PAYLOAD_YORB}
    )

    result = _run_single(script, testdata, "--filter", "another")

    assert result.returncode == 0
    assert "1 passed, 0 failed, 1 total" in result.stdout


# ---------------------------------------------------------------------------
# --scan-all: several scripts in a single report
# ---------------------------------------------------------------------------


def _multi_script_workspace(tmp_path: Path) -> tuple[Path, Path]:
    """Two scripts, each with its own fixture folder."""
    scripts = tmp_path / "script"
    scripts.mkdir()

    (scripts / "Condition.groovy").write_text(WORKING_SCRIPT, encoding="utf-8")
    (scripts / "Headers.groovy").write_text(SCRIPT_WITH_LOGGING, encoding="utf-8")

    testdata = tmp_path / "testdata"
    (testdata / "Condition").mkdir(parents=True)
    (testdata / "Headers").mkdir(parents=True)

    (testdata / "Condition" / "yorb.xml").write_text(PAYLOAD_YORB, encoding="utf-8")
    (testdata / "Condition" / "yorb.assert.json").write_text(
        '{"headers": {"OrderCodeValid": "true"}}', encoding="utf-8"
    )

    (testdata / "Headers" / "payload.xml").write_text(PAYLOAD_YORB, encoding="utf-8")
    (testdata / "Headers" / "payload.inject.json").write_text(
        '{"headers": {"Country": "US"}}', encoding="utf-8"
    )
    (testdata / "Headers" / "payload.assert.json").write_text(
        '{"log_headers": {"Country": "US"}}', encoding="utf-8"
    )

    return scripts, testdata


def test_scan_all_runs_several_scripts(tmp_path: Path):
    scripts, testdata = _multi_script_workspace(tmp_path)

    result = _run_scan(scripts, testdata)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "2 passed, 0 failed, 2 total" in result.stdout


def test_scan_all_uses_a_fixture_folder_per_script(tmp_path: Path):
    """
    Without dedicated folders, `Headers.groovy` would receive the fixtures of
    `Condition.groovy` and fail: different scripts need different inputs.
    """
    scripts, testdata = _multi_script_workspace(tmp_path)

    result = _run_scan(scripts, testdata)

    assert "Condition ::" in result.stdout
    assert "Headers ::" in result.stdout


def test_scan_all_produces_one_suite_per_script(tmp_path: Path):
    """One JUnit file, no name collision between scripts, for test-reporter."""
    scripts, testdata = _multi_script_workspace(tmp_path)
    junit = tmp_path / "junit.xml"

    result = _run_scan(scripts, testdata, "--junit", str(junit))

    assert result.returncode == 0
    root = ET.parse(junit).getroot()
    assert root.get("tests") == "2"

    suites = [s.get("name") for s in root.iter("testsuite")]
    assert sorted(suites) == ["Condition.groovy", "Headers.groovy"]


def test_scan_all_on_an_empty_folder_does_not_fail(tmp_path: Path):
    scripts = tmp_path / "script"
    scripts.mkdir()
    testdata = tmp_path / "testdata"
    testdata.mkdir()

    result = _run_scan(scripts, testdata)

    assert result.returncode == 0
    assert "0 passed, 0 failed, 0 total" in result.stdout


def test_require_fixtures_fails_when_a_script_has_no_fixture(tmp_path: Path):
    """
    In CI it makes no sense to create a snapshot: the file would be born on the
    runner and never committed, so the next run would repeat the same warning.
    We require a reviewed fixture and fail with an actionable message.
    """
    scripts, testdata = _multi_script_workspace(tmp_path)
    shutil.rmtree(testdata / "Headers")

    result = _run_scan(scripts, testdata, "--require-fixtures")

    assert result.returncode == 1
    assert "No fixture for Headers.groovy" in result.stdout
    assert "script(s) without a reviewed fixture" in result.stdout


def test_require_fixtures_passes_when_every_script_has_one(tmp_path: Path):
    scripts, testdata = _multi_script_workspace(tmp_path)

    result = _run_scan(scripts, testdata, "--require-fixtures")

    assert result.returncode == 0, result.stdout
    assert "2 passed" in result.stdout


# ---------------------------------------------------------------------------
# Snapshot: an explicit baseline, never a silent approval
# ---------------------------------------------------------------------------


def test_snapshot_without_review_fails(tmp_path: Path):
    """
    A snapshot describes what the script DOES, not what it SHOULD do. Without the
    guard, a real defect would be frozen as an expectation.
    """
    scripts = tmp_path / "script"
    scripts.mkdir()
    (scripts / "Condition.groovy").write_text(WORKING_SCRIPT, encoding="utf-8")

    testdata = tmp_path / "testdata" / "Condition"
    testdata.mkdir(parents=True)
    (testdata / "yorb.xml").write_text(PAYLOAD_YORB, encoding="utf-8")

    result = _run_scan(scripts, testdata.parent, "--snapshot-missing")

    assert result.returncode == 1
    assert "awaiting human review" in result.stdout

    snapshot = (testdata / "yorb.assert.json").read_text(encoding="utf-8")
    assert "__review__" in snapshot
    assert '"OrderCodeValid": "true"' in snapshot


def test_snapshot_after_review_passes(tmp_path: Path):
    scripts = tmp_path / "script"
    scripts.mkdir()
    (scripts / "Condition.groovy").write_text(WORKING_SCRIPT, encoding="utf-8")

    testdata = tmp_path / "testdata" / "Condition"
    testdata.mkdir(parents=True)
    (testdata / "yorb.xml").write_text(PAYLOAD_YORB, encoding="utf-8")

    # First run creates the snapshot and fails.
    _run_scan(scripts, testdata.parent, "--snapshot-missing")

    # The human reviews it: remove the pending marker.
    (testdata / "yorb.assert.json").write_text(
        '{"headers": {"OrderCodeValid": "true"}}', encoding="utf-8"
    )

    result = _run_scan(scripts, testdata.parent)

    assert result.returncode == 0, result.stdout


# ---------------------------------------------------------------------------
# The real Glovia artifact, when present
# ---------------------------------------------------------------------------

REAL_IFLOW = REPO / "CI-CD" / "iflows" / "SI_Glovia_SalesOrderCreate"
REAL_SCRIPTS = REAL_IFLOW / "src" / "main" / "resources" / "script"
REAL_FIXTURES = (
    REPO / "CI-CD" / "groovy-fixtures" / "SI_Glovia_SalesOrderCreate"
)


@pytest.mark.skipif(
    not REAL_SCRIPTS.is_dir(), reason="the real Glovia artifact is not present"
)
def test_scan_all_on_the_real_artifact_reports_the_defect():
    """
    The real condition script marks a YORB order as invalid.

    This test documents a DEFECT in the artifact, not in the harness. When the
    script is fixed in the iFlow, this test starts failing - which is intended:
    it is the signal that the fix arrived and the fixtures should be inverted.
    """
    if not REAL_FIXTURES.is_dir():
        pytest.skip(f"no reviewed fixtures at {REAL_FIXTURES}")

    result = _run_scan(REAL_SCRIPTS, REAL_FIXTURES)

    assert result.returncode == 1, (
        "the namespace defect may have been fixed in the iFlow: "
        "invert the reviewed fixtures and update the README"
    )
    assert "got 'false'" in result.stdout
