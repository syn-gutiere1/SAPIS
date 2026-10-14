"""
Tests for the CLI.

The contract with the workflow is the exit codes: a configuration error cannot
look like a test failure, and a gateway failure cannot look like success.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from pipeline import cli

IFLOW_XML = (
    '<?xml version="1.0"?><ns0:IntegrationFlow xmlns:ns0="urn:x">'
    '<integrationFlow name="FlowTeste"/></ns0:IntegrationFlow>'
)

PACKAGE = {
    "scenarios": [{"id": "TC-001", "name": "scenario"}],
    "test_data": [{"filename": "testdata/payload.xml", "content": "<x/>"}],
    "pytest_script": {"filename": "test_flow.py", "content": "def test_ok():\n    assert True\n"},
    "risks": [{"risk": "r"}],
    "coverage_analysis": {"target_coverage_percent": 100},
}


@pytest.fixture
def artifact(tmp_path: Path) -> Path:
    """Package in the SAP IS layout."""
    root = tmp_path / "SI_FlowTeste"
    resources = root / "src" / "main" / "resources"
    (resources / "scenarioflows" / "integrationflow").mkdir(parents=True)
    (resources / "script").mkdir(parents=True)
    (root / "metainfo.prop").write_text("v=1", encoding="utf-8")
    (resources / "scenarioflows" / "integrationflow" / "FlowTeste.iflw").write_text(
        IFLOW_XML, encoding="utf-8"
    )
    (resources / "script" / "Rule.groovy").write_text("// rules", encoding="utf-8")
    return root


@pytest.fixture
def environment(monkeypatch, tmp_path: Path):
    prompt = tmp_path / "prompt.txt"
    prompt.write_text("XML:\n{{flow_xml}}\nSCRIPTS:\n{{scripts}}\n", encoding="utf-8")
    monkeypatch.setenv("PORTKEY_AUTH_TOKEN", "test-token")
    monkeypatch.setenv("LLM_MODEL", "test-model")
    monkeypatch.setenv("PORTKEY_BASE_URL", "https://example")
    monkeypatch.setenv("AI_PROMPT_FILE", str(prompt))
    monkeypatch.setenv("GITHUB_SHA", "a" * 40)
    monkeypatch.setenv("GITHUB_RUN_ID", "123")
    monkeypatch.setenv("GITHUB_REPOSITORY", "org/repo")
    monkeypatch.setenv("GITHUB_REF", "refs/heads/sap-is")
    return prompt


def _fake_client(package: dict):
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(package)))],
        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5, total_tokens=15),
        model="test-model",
    )
    return SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(
                create=lambda **_: response,
                calls=[],
            )
        )
    )


# ---------------------------------------------------------------------------
# extract mode
# ---------------------------------------------------------------------------


def test_extract_prints_summary_and_returns_zero(artifact: Path, capsys):
    exit_code = cli.main(["--mode", "extract", "--source", str(artifact)])

    assert exit_code == cli.EXIT_OK
    summary = json.loads(capsys.readouterr().out)
    assert summary["iflow"] == "FlowTeste.iflw"
    assert summary["scripts"] == {"script/Rule.groovy": 8}


def test_extract_invalid_source_returns_exit_2(tmp_path: Path, capsys):
    exit_code = cli.main(["--mode", "extract", "--source", str(tmp_path)])

    assert exit_code == cli.EXIT_CONFIG
    assert "No .iflw file found" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# generate mode: configuration
# ---------------------------------------------------------------------------


def test_generate_without_token_returns_exit_2(artifact: Path, monkeypatch, capsys):
    monkeypatch.delenv("PORTKEY_AUTH_TOKEN", raising=False)
    monkeypatch.setenv("LLM_MODEL", "m")

    exit_code = cli.main(["--source", str(artifact), "--out", str(artifact / "out")])

    assert exit_code == cli.EXIT_CONFIG
    assert "PORTKEY_AUTH_TOKEN" in capsys.readouterr().err


def test_generate_invalid_artifact_returns_exit_2(tmp_path: Path, environment):
    exit_code = cli.main(["--source", str(tmp_path), "--out", str(tmp_path / "out")])

    assert exit_code == cli.EXIT_CONFIG


# ---------------------------------------------------------------------------
# generate mode: success
# ---------------------------------------------------------------------------


def test_generate_writes_artifacts_and_returns_zero(
    artifact: Path, environment, tmp_path: Path, monkeypatch, capsys
):
    monkeypatch.setattr(cli.portkey_mod, "create_client", lambda *a, **k: _fake_client(PACKAGE))

    out = tmp_path / "tests" / "FlowTeste"
    plans = tmp_path / "test-plans" / "FlowTeste"
    manifest = tmp_path / "reports" / "manifest.json"

    exit_code = cli.main(
        [
            "--source", str(artifact),
            "--out", str(out),
            "--plans", str(plans),
            "--json-report", str(manifest),
        ]
    )

    assert exit_code == cli.EXIT_OK
    assert (out / "test_flow.py").is_file()
    assert (out / "cases.json").is_file()
    assert (out / "testdata" / "payload.xml").is_file()
    assert (plans / "cases.json").is_file()
    assert (plans / "risks.json").is_file()
    assert manifest.is_file()


def test_manifest_records_provenance(
    artifact: Path, environment, tmp_path: Path, monkeypatch
):
    monkeypatch.setattr(cli.portkey_mod, "create_client", lambda *a, **k: _fake_client(PACKAGE))
    manifest_path = tmp_path / "manifest.json"

    cli.main(
        [
            "--source", str(artifact),
            "--out", str(tmp_path / "out"),
            "--json-report", str(manifest_path),
        ]
    )

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert manifest["iflow"] == "FlowTeste"
    assert len(manifest["iflow_sha256"]) == 64
    assert manifest["scripts_count"] == 1
    assert manifest["scripts_sha256"]["script/Rule.groovy"]
    assert manifest["llm"]["total_tokens"] == 15
    assert manifest["llm"]["language_code"] == "en_US"
    assert len(manifest["prompt"]["sha256"]) == 64
    assert manifest["truncated"] == []
    assert manifest["environment"]["github_sha"] == "a" * 40
    assert manifest["artifacts"]["scenarios"] == 1


def test_generate_without_json_report_still_writes_artifacts(
    artifact: Path, environment, tmp_path: Path, monkeypatch
):
    """The manifest is optional: the workflow uses --json-report, but the CLI does not require it."""
    monkeypatch.setattr(cli.portkey_mod, "create_client", lambda *a, **k: _fake_client(PACKAGE))
    out = tmp_path / "out"

    exit_code = cli.main(["--source", str(artifact), "--out", str(out)])

    assert exit_code == cli.EXIT_OK
    assert (out / "test_flow.py").is_file()
    assert not (out / "manifest.json").exists()


def test_generate_without_test_flow_returns_exit_3(
    artifact: Path, environment, tmp_path: Path, monkeypatch
):
    """A package without a pytest suite is not success: there is nothing to run."""
    package = dict(PACKAGE)
    package["pytest_script"] = ""
    monkeypatch.setattr(cli.portkey_mod, "create_client", lambda *a, **k: _fake_client(package))

    exit_code = cli.main(["--source", str(artifact), "--out", str(tmp_path / "out")])

    assert exit_code == cli.EXIT_PORTKEY


# ---------------------------------------------------------------------------
# generate mode: gateway failures
# ---------------------------------------------------------------------------


def test_gateway_failure_returns_exit_3(artifact: Path, environment, tmp_path: Path, monkeypatch, capsys):
    class BrokenClient:
        def __init__(self):
            self.chat = SimpleNamespace(
                completions=SimpleNamespace(
                    create=lambda **_: (_ for _ in ()).throw(RuntimeError("400 bad request"))
                )
            )

    monkeypatch.setattr(cli.portkey_mod, "create_client", lambda *a, **k: BrokenClient())

    exit_code = cli.main(["--source", str(artifact), "--out", str(tmp_path / "out")])

    assert exit_code == cli.EXIT_PORTKEY
    assert "Failure in the LLM call" in capsys.readouterr().err


def test_invalid_model_response_returns_exit_3(
    artifact: Path, environment, tmp_path: Path, monkeypatch
):
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="this is not json"))],
        usage=None,
        model="m",
    )
    client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **_: response))
    )
    monkeypatch.setattr(cli.portkey_mod, "create_client", lambda *a, **k: client)

    exit_code = cli.main(["--source", str(artifact), "--out", str(tmp_path / "out")])

    assert exit_code == cli.EXIT_PORTKEY


# ---------------------------------------------------------------------------
# End-to-end security
# ---------------------------------------------------------------------------


def test_malicious_fixture_does_not_escape_destination(
    artifact: Path, environment, tmp_path: Path, monkeypatch, capsys
):
    """Full path: a name coming from the model cannot write outside tests/."""
    package = dict(PACKAGE)
    package["test_data"] = [{"filename": "../../../escape.xml", "content": "<malicious/>"}]
    monkeypatch.setattr(cli.portkey_mod, "create_client", lambda *a, **k: _fake_client(package))

    out = tmp_path / "tests" / "FlowTeste"
    exit_code = cli.main(["--source", str(artifact), "--out", str(out)])

    assert exit_code == cli.EXIT_OK
    assert (out / "testdata" / "escape.xml").is_file()
    assert not (tmp_path / "escape.xml").exists()
    assert not (tmp_path.parent / "escape.xml").exists()
    assert "Fixture discarded" not in capsys.readouterr().err


def test_script_with_secret_is_cleaned_before_submission(
    artifact: Path, environment, tmp_path: Path, monkeypatch
):
    """The secret in the artifact cannot reach the prompt sent to the LLM."""
    (artifact / "src/main/resources/script/Rule.groovy").write_text(
        'def p = "password=SuperSecret123"', encoding="utf-8"
    )

    captured: dict = {}

    def _create(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(PACKAGE)))],
            usage=None,
            model="m",
        )

    client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=_create))
    )
    monkeypatch.setattr(cli.portkey_mod, "create_client", lambda *a, **k: client)

    cli.main(["--source", str(artifact), "--out", str(tmp_path / "out")])

    submitted_prompt = captured["messages"][1]["content"]
    assert "SuperSecret123" not in submitted_prompt
    assert "[REDACTED]" in submitted_prompt
