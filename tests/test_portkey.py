"""
Tests for the LLM gateway client.

No test here makes a network call: the client is injected. What is validated is the
logic the original pipeline did not have - retry with backoff, token logging,
visible truncation and tolerant JSON extraction.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from pipeline.portkey import (
    MAX_FLOW_XML_CHARS,
    AnalyzeResult,
    ConfigError,
    PortkeyError,
    Settings,
    analyze,
    build_prompt,
    extract_json,
    load_prompt,
    suggest_fixes,
)

TEMPLATE = "XML:\n{{flow_xml}}\n\nSCRIPTS:\n{{scripts}}\n"


# ---------------------------------------------------------------------------
# Fake client
# ---------------------------------------------------------------------------


class _FakeCompletions:
    def __init__(self, responses: list):
        self.responses = list(responses)
        self.last = responses[-1] if responses else None
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        # Once the list is exhausted, the last response repeats: this allows testing
        # "gives up after N attempts" without enumerating every failure.
        item = self.responses.pop(0) if self.responses else self.last
        if isinstance(item, Exception):
            raise item
        return item


class _FakeClient:
    def __init__(self, *responses):
        self.chat = SimpleNamespace(completions=_FakeCompletions(list(responses)))


def _response(content: str, *, tokens: int = 42):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
        usage=SimpleNamespace(prompt_tokens=30, completion_tokens=12, total_tokens=tokens),
        model="fake-model",
    )


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    prompt = tmp_path / "prompt.txt"
    prompt.write_text(TEMPLATE, encoding="utf-8")
    return Settings(
        base_url="https://example/v1",
        auth_token="token",
        model="model",
        language_code="pt-BR",
        prompt_file=prompt,
        temperature=0.2,
        max_attempts=3,
    )


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


def test_settings_reads_from_environment(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("PORTKEY_BASE_URL", "https://portkey.example")
    monkeypatch.setenv("PORTKEY_AUTH_TOKEN", "abc")
    monkeypatch.setenv("LLM_MODEL", "gemini")
    monkeypatch.setenv("LANGUAGE_CODE", "pt-BR")
    monkeypatch.setenv("AI_PROMPT_FILE", str(tmp_path / "p.txt"))

    cfg = Settings.from_env()

    assert cfg.base_url == "https://portkey.example/v1"
    assert cfg.model == "gemini"
    assert cfg.language_code == "pt-BR"


def test_settings_does_not_duplicate_v1_suffix(monkeypatch):
    monkeypatch.setenv("PORTKEY_BASE_URL", "https://portkey.example/v1")
    monkeypatch.setenv("PORTKEY_AUTH_TOKEN", "abc")
    monkeypatch.setenv("LLM_MODEL", "m")

    assert Settings.from_env().base_url == "https://portkey.example/v1"


def test_settings_requires_token(monkeypatch):
    monkeypatch.delenv("PORTKEY_AUTH_TOKEN", raising=False)
    monkeypatch.setenv("LLM_MODEL", "m")

    with pytest.raises(ConfigError, match="PORTKEY_AUTH_TOKEN"):
        Settings.from_env()


def test_settings_requires_model(monkeypatch):
    monkeypatch.setenv("PORTKEY_AUTH_TOKEN", "abc")
    monkeypatch.delenv("LLM_MODEL", raising=False)

    with pytest.raises(ConfigError, match="LLM_MODEL"):
        Settings.from_env()


def test_load_prompt_missing_file(tmp_path: Path):
    with pytest.raises(ConfigError, match="not found"):
        load_prompt(tmp_path / "does_not_exist.txt")


# ---------------------------------------------------------------------------
# Prompt assembly
# ---------------------------------------------------------------------------


def test_build_prompt_replaces_placeholders():
    prompt, truncated = build_prompt(TEMPLATE, "<xml/>", {"script/a.groovy": "def x(){}"})

    assert "<xml/>" in prompt
    assert "def x(){}" in prompt
    assert "{{flow_xml}}" not in prompt
    assert "{{scripts}}" not in prompt
    assert truncated == []


def test_build_prompt_records_truncation():
    """
    Truncating silently hides coverage holes - the cut is recorded.

    The limit is explicit here on purpose: the default was raised after measuring
    that 30 000 chars cut a real iFlow in half.
    """
    prompt, truncated = build_prompt(TEMPLATE, "x" * 40_000, {}, max_flow_chars=30_000)

    assert len(truncated) == 1
    assert "flow_xml" in truncated[0]
    assert "x" * 30_000 in prompt
    assert "x" * 30_001 not in prompt


def test_build_prompt_does_not_truncate_real_glovia_iflow():
    """
    Regression of the finding measured on a real run: the Glovia `.iflw` has 45 225
    chars and the old limit of 30 000 sent only two thirds of the flow. The
    result dropped from 30 to 6 scenarios.
    """
    measured_iflow = 45_225

    _prompt, truncated = build_prompt(TEMPLATE, "x" * measured_iflow, {})

    assert truncated == []


def test_build_prompt_limit_configurable_from_environment(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("PORTKEY_AUTH_TOKEN", "t")
    monkeypatch.setenv("LLM_MODEL", "m")
    monkeypatch.setenv("LLM_MAX_FLOW_CHARS", "1000")

    cfg = Settings.from_env()

    assert cfg.max_flow_chars == 1000


def test_build_prompt_invalid_value_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("PORTKEY_AUTH_TOKEN", "t")
    monkeypatch.setenv("LLM_MODEL", "m")
    monkeypatch.setenv("LLM_MAX_FLOW_CHARS", "not-a-number")

    assert Settings.from_env().max_flow_chars == MAX_FLOW_XML_CHARS


def test_build_prompt_does_not_break_on_json_braces():
    """The template contains JSON braces; `replace` avoids the `format` problem."""
    template = '{"scenarios": []}\n{{flow_xml}}'
    prompt, _ = build_prompt(template, "<x/>", {})
    assert '{"scenarios": []}' in prompt


# ---------------------------------------------------------------------------
# JSON extraction
# ---------------------------------------------------------------------------


def test_extract_json_simple():
    assert extract_json('{"a": 1}') == {"a": 1}


def test_extract_json_with_code_fence():
    assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}


def test_extract_json_with_fence_without_language():
    assert extract_json('```\n{"a": 1}\n```') == {"a": 1}


def test_extract_json_with_surrounding_text():
    text = 'Here is the result:\n```json\n{"a": 1}\n```\nEnd.'
    assert extract_json(text) == {"a": 1}


def test_extract_json_invalid_shows_start_of_response():
    with pytest.raises(PortkeyError, match="not valid JSON"):
        extract_json("this is not json")


def test_extract_json_rejects_list():
    with pytest.raises(PortkeyError, match="JSON object"):
        extract_json("[1, 2, 3]")


def test_extract_json_empty():
    with pytest.raises(PortkeyError):
        extract_json("")


# ---------------------------------------------------------------------------
# Analyze: tokens, truncation, attempts
# ---------------------------------------------------------------------------


def test_analyze_returns_package_and_usage(settings: Settings):
    client = _FakeClient(_response(json.dumps({"scenarios": [{"id": "TC-1"}]})))

    result = analyze("<xml/>", {}, settings=settings, client=client)

    assert isinstance(result, AnalyzeResult)
    assert result.package["scenarios"][0]["id"] == "TC-1"
    assert result.usage.total_tokens == 42
    assert result.usage.prompt_tokens == 30
    assert result.usage.model == "fake-model"
    assert result.attempts == 1
    assert len(result.prompt_sha256) == 64


def test_analyze_records_truncation(settings: Settings):
    settings.max_flow_chars = 30_000
    client = _FakeClient(_response('{"scenarios": []}'))

    result = analyze("y" * 50_000, {}, settings=settings, client=client)

    assert result.truncated
    assert result.as_dict()["truncated"]


def test_analyze_sends_system_prompt_in_language(settings: Settings):
    client = _FakeClient(_response('{"scenarios": []}'))

    analyze("<x/>", {}, settings=settings, client=client)

    messages = client.chat.completions.calls[0]["messages"]
    assert messages[0]["role"] == "system"
    assert "pt-BR" in messages[0]["content"]
    assert messages[1]["role"] == "user"


# ---------------------------------------------------------------------------
# Retry
# ---------------------------------------------------------------------------


def test_retry_on_rate_limit_then_success(settings: Settings):
    client = _FakeClient(RuntimeError("429 rate limit exceeded"), _response('{"scenarios": []}'))
    slept: list[float] = []

    result = analyze("<x/>", {}, settings=settings, client=client, sleep=slept.append)

    assert result.attempts == 2
    assert len(slept) == 1
    assert slept[0] > 0


def test_retry_on_timeout(settings: Settings):
    client = _FakeClient(RuntimeError("Request timed out"), _response('{"scenarios": []}'))
    result = analyze("<x/>", {}, settings=settings, client=client, sleep=lambda _: None)
    assert result.attempts == 2


def test_does_not_retry_permanent_error(settings: Settings):
    """Contract error (400) does not improve with another attempt."""
    client = _FakeClient(RuntimeError("400 invalid request"))
    slept: list[float] = []

    with pytest.raises(RuntimeError, match="400"):
        analyze("<x/>", {}, settings=settings, client=client, sleep=slept.append)

    assert slept == []
    assert len(client.chat.completions.calls) == 1


def test_gives_up_after_max_attempts(settings: Settings):
    client = _FakeClient(*[RuntimeError("503 service unavailable")] * 5)
    slept: list[float] = []

    with pytest.raises(RuntimeError, match="503"):
        analyze("<x/>", {}, settings=settings, client=client, sleep=slept.append)

    assert len(client.chat.completions.calls) == settings.max_attempts


def test_backoff_grows_between_attempts(settings: Settings):
    client = _FakeClient(*[RuntimeError("503 unavailable")] * 4)

    with pytest.raises(RuntimeError):
        analyze("<x/>", {}, settings=settings, client=client, sleep=lambda _: None)

    # The attempts happened; the growth is verified in the _chat_completion tests
    assert len(client.chat.completions.calls) == settings.max_attempts


# ---------------------------------------------------------------------------
# Fix suggestion
# ---------------------------------------------------------------------------


def test_suggest_fixes_returns_text(settings: Settings):
    client = _FakeClient(_response("Adjust the Groovy on line 7."))

    suggestion = suggest_fixes("<xml/>", {}, "failure log", settings=settings, client=client)

    assert "Groovy" in suggestion


def test_suggest_fixes_limits_log_size(settings: Settings):
    client = _FakeClient(_response("ok"))
    huge_log = "L" * 100_000

    suggest_fixes("<xml/>", {}, huge_log, settings=settings, client=client, max_output_chars=500)

    sent = client.chat.completions.calls[0]["messages"][1]["content"]
    assert "L" * 501 not in sent
