"""
Tests for `derive_endpoint.py`.

The point of this module is that the per-iFlow trigger path comes from the
artifact, so nobody has to type a URL for every flow. These tests cover the
adapter shapes that actually occur, and the case that has no webhook at all.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"

if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import derive_endpoint  # noqa: E402


def _artifact(tmp_path: Path, name: str, sender_props: str, *, sender_name: str = "EndpointSender") -> Path:
    """Build a minimal `.iflw` with one sender participant and its properties."""
    props = "\n".join(
        f"""                <ifl:property>
                    <key>{key}</key>
                    <value>{value}</value>
                </ifl:property>"""
        for key, value in sender_props
    )

    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<bpmn2:definitions xmlns:bpmn2="http://www.omg.org/spec/BPMN/20100524/MODEL"
                   xmlns:ifl="http:///com.sap.ifl.model/Ifl.xsd">
    <bpmn2:collaboration>
        <bpmn2:participant id="Participant_1" ifl:type="{sender_name}" name="Sender">
            <bpmn2:extensionElements>
{props}
            </bpmn2:extensionElements>
        </bpmn2:participant>
    </bpmn2:collaboration>
</bpmn2:definitions>
"""
    target = tmp_path / f"{name}.iflw"
    target.write_text(xml, encoding="utf-8")
    return target


SOAP_SENDER = [
    ("ComponentType", "SOAP"),
    ("address", "/Glovia_SalesOrder"),
    ("TransportProtocol", "HTTP"),
    ("senderAuthType", "RoleBased"),
    ("userRole", "ESBMessaging.send"),
]

REST_SENDER = [
    ("ComponentType", "REST"),
    ("urlPath", "/api/v1/orders"),
    ("TransportProtocol", "HTTPS"),
]

SFTP_SENDER = [
    ("ComponentType", "SFTP"),
    ("address", "/inbound/orders"),
    ("TransportProtocol", "SFTP"),
]


# ---------------------------------------------------------------------------
# HTTP triggers
# ---------------------------------------------------------------------------


def test_derives_soap_listener_path(tmp_path: Path):
    artifact = _artifact(tmp_path, "SoapFlow", SOAP_SENDER)

    result = derive_endpoint.derive(artifact)

    assert result["has_http_trigger"] is True
    assert result["component_type"] == "SOAP"
    assert result["listener_path"] == "/Glovia_SalesOrder"


def test_derives_rest_url_path(tmp_path: Path):
    """REST adapters declare `urlPath`, not `address`."""
    artifact = _artifact(tmp_path, "RestFlow", REST_SENDER)

    result = derive_endpoint.derive(artifact)

    assert result["has_http_trigger"] is True
    assert result["listener_path"] == "/api/v1/orders"


def test_reports_sender_auth_and_required_role(tmp_path: Path):
    """
    The artifact states how the endpoint authenticates, which is exactly what a
    UAT test needs to know before calling it.
    """
    artifact = _artifact(tmp_path, "SoapFlow", SOAP_SENDER)

    result = derive_endpoint.derive(artifact)

    assert result["sender_auth_type"] == "RoleBased"
    assert result["required_role"] == "ESBMessaging.send"


def test_transport_protocol_is_reported(tmp_path: Path):
    artifact = _artifact(tmp_path, "RestFlow", REST_SENDER)

    assert derive_endpoint.derive(artifact)["transport_protocol"] == "HTTPS"


# ---------------------------------------------------------------------------
# No webhook
# ---------------------------------------------------------------------------


def test_sftp_sender_has_no_http_trigger(tmp_path: Path):
    """
    A polled adapter has no webhook. Reporting this honestly is what lets the
    caller skip the smoke test instead of inventing a URL.
    """
    artifact = _artifact(tmp_path, "SftpFlow", SFTP_SENDER)

    result = derive_endpoint.derive(artifact)

    assert result["has_http_trigger"] is False
    assert result["listener_path"] is None
    assert result["component_type"] == "SFTP"


def test_http_sender_without_path_has_no_trigger(tmp_path: Path):
    """A listener with no declared path cannot be called."""
    artifact = _artifact(tmp_path, "NoPathFlow", [("ComponentType", "SOAP")])

    assert derive_endpoint.derive(artifact)["has_http_trigger"] is False


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


def test_missing_artifact_raises(tmp_path: Path):
    with pytest.raises(derive_endpoint.DeriveError, match="not found"):
        derive_endpoint.derive(tmp_path / "missing.iflw")


def test_artifact_without_sender_raises(tmp_path: Path):
    target = tmp_path / "NoSender.iflw"
    target.write_text("<bpmn2:definitions/>", encoding="utf-8")

    with pytest.raises(derive_endpoint.DeriveError, match="no EndpointSender"):
        derive_endpoint.derive(target)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPTS / "derive_endpoint.py"), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def test_cli_accepts_the_artifact_directory(tmp_path: Path):
    _artifact(tmp_path, "SoapFlow", SOAP_SENDER)

    result = _run(str(tmp_path))

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["listener_path"] == "/Glovia_SalesOrder"


def test_cli_accepts_the_iflw_itself(tmp_path: Path):
    artifact = _artifact(tmp_path, "SoapFlow", SOAP_SENDER)

    result = _run(str(artifact))

    assert result.returncode == 0
    assert json.loads(result.stdout)["iflow"] == "SoapFlow"


def test_cli_reports_no_trigger_but_exits_zero(tmp_path: Path):
    _artifact(tmp_path, "SftpFlow", SFTP_SENDER)

    result = _run(str(tmp_path))

    assert result.returncode == 0
    assert "no HTTP trigger" in result.stderr


def test_cli_can_fail_when_there_is_no_trigger(tmp_path: Path):
    _artifact(tmp_path, "SftpFlow", SFTP_SENDER)

    result = _run(str(tmp_path), "--fail-if-no-trigger")

    assert result.returncode == 1


def test_cli_missing_artifact_returns_exit_2(tmp_path: Path):
    result = _run(str(tmp_path / "missing"))

    assert result.returncode == 2


# ---------------------------------------------------------------------------
# The real Glovia artifact, when present
# ---------------------------------------------------------------------------

REAL_ARTIFACT = REPO / "CI-CD" / "iflows" / "SI_Glovia_SalesOrderCreate"


@pytest.mark.skipif(not REAL_ARTIFACT.is_dir(), reason="the real Glovia artifact is not present")
def test_derives_the_real_glovia_trigger():
    """
    Pins the derivation against the artifact the pipeline was built for: the
    sender is a SOAP listener at /Glovia_SalesOrder, which is the path a UAT
    smoke test has to call.
    """
    result = derive_endpoint.derive(
        REAL_ARTIFACT
        / "src"
        / "main"
        / "resources"
        / "scenarioflows"
        / "integrationflow"
        / "SI_Glovia_SalesOrderCreate.iflw"
    )

    assert result["has_http_trigger"] is True
    assert result["component_type"] == "SOAP"
    assert result["listener_path"] == "/Glovia_SalesOrder"
    assert result["required_role"] == "ESBMessaging.send"


# ---------------------------------------------------------------------------
# Externalized parameters
# ---------------------------------------------------------------------------

EXTERNALIZED_ADDRESS_SENDER = [
    ("ComponentType", "SOAP"),
    ("address", "{{Glovia_Address}}"),
    ("TransportProtocol", "HTTP"),
]

EXTERNALIZED_TYPE_SENDER = [
    ("ComponentType", "{{Glovia_Protocol}}"),
    ("address", "/Glovia_SalesOrder"),
    ("TransportProtocol", "HTTP"),
]

PARTIALLY_EXTERNALIZED_PATH_SENDER = [
    ("ComponentType", "REST"),
    ("urlPath", "/{{Glovia_Version}}/orders"),
    ("TransportProtocol", "HTTPS"),
]

EXTERNALIZED_AUTH_SENDER = [
    ("ComponentType", "SOAP"),
    ("address", "/Glovia_SalesOrder"),
    ("TransportProtocol", "HTTP"),
    ("senderAuthType", "{{Glovia_AuthType}}"),
]


def test_externalized_address_yields_no_usable_path(tmp_path: Path):
    """
    The defect: `{{Glovia_Address}}` was returned as the listener path, so a caller building
    `host + path` produced `https://tenant.invalid{{Glovia_Address}}`. The value lives in
    the tenant, not in the artifact, so it cannot be derived - and must not be pretended.
    """
    artifact = _artifact(tmp_path, "ExternalAddress", EXTERNALIZED_ADDRESS_SENDER)

    result = derive_endpoint.derive(artifact)

    assert result["listener_path"] is None
    assert result["has_http_trigger"] is not True
    assert result["unresolved_parameters"] == ["Glovia_Address"]
    # The raw value is still reported, for the message that names the parameter.
    assert result["path"] == "{{Glovia_Address}}"


def test_externalized_parameter_inside_a_longer_path_is_caught(tmp_path: Path):
    """A placeholder in the middle of a path is just as unusable as a whole-value one."""
    artifact = _artifact(tmp_path, "PartialPath", PARTIALLY_EXTERNALIZED_PATH_SENDER)

    result = derive_endpoint.derive(artifact)

    assert result["listener_path"] is None
    assert result["unresolved_parameters"] == ["Glovia_Version"]


def test_externalized_component_type_reports_unknown_not_absent(tmp_path: Path):
    """
    An externalized `ComponentType` is a flow whose trigger depends on the tenant value. It
    was read as the literal `{{Glovia_Protocol}}`, matched no HTTP type, and was reported as
    "this flow declares no HTTP trigger" - the opposite of the truth, and silently.
    """
    artifact = _artifact(tmp_path, "ExternalType", EXTERNALIZED_TYPE_SENDER)

    result = derive_endpoint.derive(artifact)

    assert result["has_http_trigger"] is None
    assert result["listener_path"] is None
    assert result["unresolved_parameters"] == ["Glovia_Protocol"]


def test_several_parameters_are_all_reported_once_each(tmp_path: Path):
    artifact = _artifact(
        tmp_path,
        "TwoParams",
        [
            ("ComponentType", "SOAP"),
            ("address", "/{{Glovia_Tenant}}/{{Glovia_Service}}"),
            ("TransportProtocol", "HTTP"),
        ],
    )

    result = derive_endpoint.derive(artifact)

    assert result["unresolved_parameters"] == ["Glovia_Tenant", "Glovia_Service"]


def test_an_externalized_value_that_is_not_the_path_is_not_a_finding(tmp_path: Path):
    """
    `senderAuthType` externalized does not affect whether the endpoint can be called, so the
    path stays usable. Reporting it would be noise.
    """
    artifact = _artifact(tmp_path, "ExternalAuth", EXTERNALIZED_AUTH_SENDER)

    result = derive_endpoint.derive(artifact)

    assert result["unresolved_parameters"] == []
    assert result["listener_path"] == "/Glovia_SalesOrder"
    assert result["has_http_trigger"] is True


def test_a_literal_path_has_no_unresolved_parameters(tmp_path: Path):
    artifact = _artifact(tmp_path, "LiteralSoap", SOAP_SENDER)

    result = derive_endpoint.derive(artifact)

    assert result["unresolved_parameters"] == []
    assert result["listener_path"] == "/Glovia_SalesOrder"


def test_cli_fails_when_a_parameter_is_unresolved(tmp_path: Path):
    """Exit 2, not 0: no usable URL exists, so a caller must not proceed."""
    _artifact(tmp_path, "ExternalAddress", EXTERNALIZED_ADDRESS_SENDER)

    result = _run(str(tmp_path))

    assert result.returncode == 2
    assert "Glovia_Address" in result.stderr
    assert "externalized" in result.stderr


def test_cli_does_not_warn_about_a_missing_trigger_when_the_type_is_unknown(tmp_path: Path):
    """The message must not claim the flow has no trigger when that is unknown."""
    _artifact(tmp_path, "ExternalType", EXTERNALIZED_TYPE_SENDER)

    result = _run(str(tmp_path))

    assert result.returncode == 2
    assert "declares no HTTP trigger" not in result.stderr
    assert "Glovia_Protocol" in result.stderr
