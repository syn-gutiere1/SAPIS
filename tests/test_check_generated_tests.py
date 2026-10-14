"""
Tests for the checker of generated tests.

The prompt asks for mocks and no network, but a prompt is an instruction, not a
guarantee. The checker turns the instruction into a mechanical check, and the
case that motivated it is in the first test.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

HUB = Path(__file__).resolve().parents[1]
SCRIPTS = HUB / "scripts"

if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import check_generated_tests as checker  # noqa: E402


def _write(tmp_path: Path, content: str) -> Path:
    target = tmp_path / "test_flow.py"
    target.write_text(content, encoding="utf-8")
    return target


# ---------------------------------------------------------------------------
# The case that motivated the checker
# ---------------------------------------------------------------------------

REAL_CI_TEST = '''
import pytest
import requests
from unittest.mock import MagicMock, patch

@patch('requests.post')
def test_mocked_http_sender_transport(mock_post, valid_so_payload):
    mock_response = MagicMock()
    mock_response.status_code = 202
    mock_post.return_value = mock_response

    session = requests.Session()
    response = session.post(
        'https://cpi-dev.company.corp/cxf/Glovia_SalesOrder',
        data=valid_so_payload,
        headers={'Content-Type': 'text/xml'},
        timeout=10
    )
    assert response.status_code == 202
'''


def test_detects_the_invented_host_from_the_real_run(tmp_path: Path):
    """
    `cpi-dev.company.corp` existed neither in the artifact nor in the prompt: the
    model invented it. Here the check is mechanical, it does not depend on DNS
    failing.
    """
    path = _write(tmp_path, REAL_CI_TEST)

    findings = checker.check(path)

    hosts = [f.detail for f in findings if f.category == "host"]
    assert any("cpi-dev.company.corp" in h for h in hosts)
    assert all("schemas.xmlsoap.org" not in h for h in hosts)


def test_does_not_flag_xml_namespace(tmp_path: Path):
    """SOAP namespaces appear legitimately in fixtures; they are not a connection."""
    content = '''
NS = "http://schemas.xmlsoap.org/soap/envelope/"
NS2 = "http://soap.sforce.com/2005/09/outbound"
NS3 = "http://www.w3.org/2001/XMLSchema-instance"

def test_constants_only():
    assert "soap" in NS
'''
    assert checker.check(_write(tmp_path, content)) == []


def test_does_not_flag_documentation_hosts(tmp_path: Path):
    content = '''
def test_documentation_address():
    url = "https://example.invalid/cxf/Flow"
    other = "http://example.com/api"
    assert "invalid" in url and "example" in other
'''
    assert checker.check(_write(tmp_path, content)) == []


def test_does_not_flag_localhost_nor_loopback(tmp_path: Path):
    content = '''
@responses.activate
def test_local():
    responses.add(responses.POST, "http://localhost:8080/flow", status=200)
    assert requests.post("http://127.0.0.1:8080/flow").status_code == 200
'''
    assert checker.check(_write(tmp_path, content)) == []


# ---------------------------------------------------------------------------
# Network without mock
# ---------------------------------------------------------------------------


def test_flags_network_call_without_mock(tmp_path: Path):
    content = '''
import requests

def test_without_mock():
    assert requests.post("http://localhost:8080/x").status_code == 200
'''
    findings = checker.check(_write(tmp_path, content))

    assert any(f.category == "network" for f in findings)


def test_does_not_flag_network_when_there_is_a_mock(tmp_path: Path):
    content = '''
import responses
import requests

@responses.activate
def test_with_mock():
    responses.add(responses.POST, "http://localhost:8080/x", status=200)
    assert requests.post("http://localhost:8080/x").status_code == 200
'''
    findings = checker.check(_write(tmp_path, content))

    assert not any(f.category == "network" for f in findings)


def test_flags_session_without_mock(tmp_path: Path):
    """`requests.Session().post` is not intercepted by `@patch('requests.post')`."""
    content = '''
import requests

def test_session():
    return requests.Session().post("http://localhost:8080/x")
'''
    findings = checker.check(_write(tmp_path, content))

    assert any("requests.Session" in f.detail or f.category == "network" for f in findings)


# ---------------------------------------------------------------------------
# Fixed port
# ---------------------------------------------------------------------------


def test_flags_fixed_port(tmp_path: Path):
    content = '''
def test_server():
    server = HTTPServer(("localhost", 18080), Handler)
    assert server is not None
'''
    findings = checker.check(_write(tmp_path, content))

    assert any(f.category == "port" and "18080" in f.detail for f in findings)


def test_accepts_ephemeral_port(tmp_path: Path):
    content = '''
def test_ephemeral_server():
    server = HTTPServer(("localhost", 0), Handler)
    assert server.server_port > 0
'''
    findings = checker.check(_write(tmp_path, content))

    assert not any(f.category == "port" for f in findings)


def test_accepts_port_from_environment_variable(tmp_path: Path):
    content = '''
MOCK_PORT = int(os.environ.get("MOCK_PORT", "0"))

def test_server():
    server = HTTPServer(("localhost", MOCK_PORT), Handler)
'''
    findings = checker.check(_write(tmp_path, content))

    assert not any(f.category == "port" for f in findings)


# ---------------------------------------------------------------------------
# Configurable hosts
# ---------------------------------------------------------------------------


def test_extra_suffix_from_environment_variable(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("FORBIDDEN_HOST_SUFFIXES", ".syngenta.com")
    content = 'URL = "https://cpi.syngenta.com/api"'

    findings = checker.check(_write(tmp_path, content))

    assert any("syngenta.com" in f.detail for f in findings)


def test_allow_specific_host(tmp_path: Path):
    content = 'URL = "https://cpi.homolog.interno.com/api"'

    without_permission = checker.check(_write(tmp_path, content))
    assert without_permission

    with_permission = checker.check(
        _write(tmp_path, content), {"cpi.homolog.interno.com"}
    )
    assert with_permission == []


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPTS / "check_generated_tests.py"), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def test_cli_clean_file_returns_zero(tmp_path: Path):
    path = _write(tmp_path, "def test_ok():\n    assert True\n")

    result = _run(str(path))

    assert result.returncode == 0
    assert "OK" in result.stdout


def test_cli_suspicious_file_returns_one(tmp_path: Path):
    path = _write(tmp_path, REAL_CI_TEST)

    result = _run(str(path))

    assert result.returncode == 1
    assert "cpi-dev.company.corp" in result.stdout


def test_cli_missing_file_returns_two(tmp_path: Path):
    result = _run(str(tmp_path / "missing.py"))

    assert result.returncode == 2


def test_cli_allow_suppresses_the_finding(tmp_path: Path):
    path = _write(tmp_path, 'URL = "https://cpi.homolog.interno.com/x"')

    result = _run(str(path), "--permitir", "cpi.homolog.interno.com")

    assert result.returncode == 0
