"""
portkey.py - LLM gateway client and prompt assembly.

Access is still through the `openai` SDK with `base_url` pointing at Portkey,
exactly as in the original pipeline. What changes:

  - configuration read from the environment at call time (never from a `.env` written
    on the runner);
  - retry with exponential backoff on transient failures (rate limit, 5xx,
    timeout), which the original did not have;
  - token/cost logging per call, for the provenance manifest;
  - removal of the ```json``` fences and JSON validation with a useful error message.

Nothing here writes to disk or knows the artifact format: that belongs to the `writer`.
"""

from __future__ import annotations

import json
import os
import random
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DEFAULT_BASE_URL = "https://api.portkey.ai/v1"
DEFAULT_PROMPT_FILE = "prompt/prompt_template.txt"

# Truncation inherited from the original pipeline. The limit was a fixed 30 000 chars
# and the cut happened silently; measured on a real iFlow, that means sending only
# two thirds of the flow and generating 6 scenarios instead of 30.
#
# It is now configurable through `LLM_MAX_FLOW_CHARS`, and truncation is always
# recorded in the manifest and in the Job Summary. The default value was raised because
# the model used in production accepts the full input; anyone who needs the old
# behavior sets the variable.
MAX_FLOW_XML_CHARS = 120_000
MAX_SCRIPTS_CHARS = 120_000

# Total attempts (1 attempt + retries).
DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_BACKOFF_BASE = 2.0
DEFAULT_BACKOFF_MAX = 30.0


class PortkeyError(Exception):
    """Failure while talking to the LLM gateway."""


class ConfigError(Exception):
    """Missing or invalid configuration."""


@dataclass
class LlmUsage:
    """Usage observed in one call, for the manifest."""

    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    model: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "model": self.model,
        }


@dataclass
class AnalyzeResult:
    """Result of an analysis: the model package and the run metadata."""

    package: dict[str, Any]
    usage: LlmUsage = field(default_factory=LlmUsage)
    prompt_sha256: str = ""
    truncated: list[str] = field(default_factory=list)
    attempts: int = 1
    duration_seconds: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "usage": self.usage.as_dict(),
            "prompt_sha256": self.prompt_sha256,
            "truncated": self.truncated,
            "attempts": self.attempts,
            "duration_seconds": round(self.duration_seconds, 3),
        }


@dataclass
class Settings:
    """Configuration resolved from the environment."""

    base_url: str
    auth_token: str
    model: str
    language_code: str
    prompt_file: Path
    temperature: float
    max_attempts: int
    max_flow_chars: int = MAX_FLOW_XML_CHARS
    max_scripts_chars: int = MAX_SCRIPTS_CHARS

    @classmethod
    def from_env(cls, *, require_token: bool = True) -> "Settings":
        base_url = os.environ.get("PORTKEY_BASE_URL", DEFAULT_BASE_URL).strip()
        if not base_url:
            base_url = DEFAULT_BASE_URL
        if not base_url.endswith("/v1"):
            base_url = f"{base_url}/v1"

        auth_token = os.environ.get("PORTKEY_AUTH_TOKEN", "").strip()
        model = os.environ.get("LLM_MODEL", "").strip()

        errors: list[str] = []
        if require_token and not auth_token:
            errors.append("PORTKEY_AUTH_TOKEN is not defined in the environment")
        if not model:
            errors.append("LLM_MODEL is not defined in the environment")
        if errors:
            raise ConfigError("Invalid configuration:\n  - " + "\n  - ".join(errors))

        prompt_file = Path(
            os.environ.get("AI_PROMPT_FILE", DEFAULT_PROMPT_FILE).strip() or DEFAULT_PROMPT_FILE
        )

        try:
            temperature = float(os.environ.get("LLM_TEMPERATURE", "0.2"))
        except ValueError:
            temperature = 0.2

        try:
            max_attempts = max(1, int(os.environ.get("LLM_MAX_ATTEMPTS", DEFAULT_MAX_ATTEMPTS)))
        except ValueError:
            max_attempts = DEFAULT_MAX_ATTEMPTS

        def _int_env(name: str, default: int) -> int:
            try:
                value = int(os.environ.get(name, default))
            except ValueError:
                return default
            return value if value > 0 else default

        return cls(
            base_url=base_url,
            auth_token=auth_token,
            model=model,
            language_code=os.environ.get("LANGUAGE_CODE", "en_US").strip() or "en_US",
            prompt_file=prompt_file,
            temperature=temperature,
            max_attempts=max_attempts,
            max_flow_chars=_int_env("LLM_MAX_FLOW_CHARS", MAX_FLOW_XML_CHARS),
            max_scripts_chars=_int_env("LLM_MAX_SCRIPTS_CHARS", MAX_SCRIPTS_CHARS),
        )


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------


def load_prompt(prompt_file: str | Path) -> str:
    """Reads the prompt template from disk, with a clear error if it does not exist."""
    path = Path(prompt_file)
    if not path.is_file():
        raise ConfigError(
            f"Prompt template not found: {path}. Set AI_PROMPT_FILE "
            "to point at the template in the hub checkout."
        )
    return path.read_text(encoding="utf-8")


def build_prompt(
    template: str,
    flow_xml: str,
    scripts: dict[str, str],
    *,
    max_flow_chars: int = MAX_FLOW_XML_CHARS,
    max_scripts_chars: int = MAX_SCRIPTS_CHARS,
) -> tuple[str, list[str]]:
    """
    Fills the template with the XML and the scripts.

    Uses `replace()` and not `str.format()`, so it does not clash with the JSON
    braces the template itself contains. Returns the prompt and the list of truncated
    fields (so the manifest records it, instead of hiding it).
    """
    truncated: list[str] = []

    if len(flow_xml) > max_flow_chars:
        truncated.append(f"flow_xml ({len(flow_xml)} -> {max_flow_chars} chars)")
        flow_xml = flow_xml[:max_flow_chars]

    scripts_json = json.dumps(scripts, ensure_ascii=False)
    if len(scripts_json) > max_scripts_chars:
        truncated.append(f"scripts ({len(scripts_json)} -> {max_scripts_chars} chars)")
        scripts_json = scripts_json[:max_scripts_chars]

    prompt = template.replace("{{flow_xml}}", flow_xml).replace("{{scripts}}", scripts_json)
    return prompt, truncated


def extract_json(content: str) -> dict[str, Any]:
    """
    Converts the model response into a dict, removing code fences.

    Raises `PortkeyError` with the start of the response when it is not valid JSON -
    without that, the original error was a `JSONDecodeError` with no context.
    """
    text = (content or "").strip()

    if "```json" in text:
        text = text.split("```json", 1)[1].split("```", 1)[0].strip()
    elif "```" in text:
        text = text.split("```", 1)[1].split("```", 1)[0].strip()

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        preview = text[:500].replace("\n", "\\n")
        raise PortkeyError(
            f"Model response is not valid JSON: {exc}. Start of the response: {preview}"
        ) from exc

    if not isinstance(parsed, dict):
        raise PortkeyError(
            f"Model response should be a JSON object, got {type(parsed).__name__}."
        )
    return parsed


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


def create_client(settings: Settings, *, client: Any | None = None) -> Any:
    """Creates the OpenAI client pointing at Portkey (or returns the injected one)."""
    if client is not None:
        return client
    try:
        from openai import OpenAI
    except ImportError as exc:  # pragma: no cover - declared dependency
        raise ConfigError(
            "Package 'openai' is not installed. Run: pip install -r requirements-ci.txt"
        ) from exc

    return OpenAI(api_key=settings.auth_token, base_url=settings.base_url)


def _is_retryable(exc: Exception) -> bool:
    """Decides whether the failure is worth another attempt."""
    text = f"{type(exc).__name__}: {exc}".lower()
    markers = (
        "rate limit",
        "ratelimit",
        "429",
        "timeout",
        "timed out",
        "temporarily",
        "connection",
        "502",
        "503",
        "504",
        "overloaded",
    )
    return any(marker in text for marker in markers)


def _chat_completion(
    client: Any,
    settings: Settings,
    messages: list[dict[str, str]],
    *,
    temperature: float,
    sleep=time.sleep,
    max_attempts: int | None = None,
) -> tuple[str, LlmUsage, int]:
    """
    Runs the call with retry and exponential backoff.

    Returns `(content, usage, attempts)`.
    """
    attempts_allowed = max_attempts or settings.max_attempts
    last_error: Exception | None = None

    for attempt in range(1, attempts_allowed + 1):
        try:
            response = client.chat.completions.create(
                model=settings.model,
                messages=messages,
                temperature=temperature,
            )
        except Exception as exc:  # noqa: BLE001 - re-raised or retried
            last_error = exc
            if attempt >= attempts_allowed or not _is_retryable(exc):
                raise
            delay = min(DEFAULT_BACKOFF_BASE ** attempt, DEFAULT_BACKOFF_MAX)
            delay += random.uniform(0, 0.5)  # jitter, avoids synchronized retries
            print(
                f"[Portkey] attempt {attempt}/{attempts_allowed} failed ({exc}). "
                f"Retrying in {delay:.1f}s."
            )
            sleep(delay)
            continue

        content = response.choices[0].message.content or ""
        usage = LlmUsage()
        raw_usage = getattr(response, "usage", None)
        if raw_usage is not None:
            usage.prompt_tokens = getattr(raw_usage, "prompt_tokens", None)
            usage.completion_tokens = getattr(raw_usage, "completion_tokens", None)
            usage.total_tokens = getattr(raw_usage, "total_tokens", None)
        usage.model = getattr(response, "model", None) or settings.model
        return content, usage, attempt

    raise PortkeyError(f"All {attempts_allowed} attempts failed: {last_error}")


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------


def analyze(
    flow_xml: str,
    scripts: dict[str, str],
    *,
    settings: Settings | None = None,
    client: Any | None = None,
    sleep=time.sleep,
) -> AnalyzeResult:
    """
    Sends the iFlow (already cleaned of secrets) to the model and returns the test package.
    """
    import hashlib

    settings = settings or Settings.from_env()
    template = load_prompt(settings.prompt_file)
    prompt, truncated = build_prompt(
        template,
        flow_xml,
        scripts,
        max_flow_chars=settings.max_flow_chars,
        max_scripts_chars=settings.max_scripts_chars,
    )

    system_prompt = (
        "You are a senior SAP integration engineer. Always answer with valid JSON. "
        "Write all your response content, descriptions, comments, risk explanations, "
        "and test case names in the language corresponding to the ISO code "
        f"{settings.language_code}."
    )

    resolved_client = create_client(settings, client=client)

    started = time.monotonic()
    content, usage, attempts = _chat_completion(
        resolved_client,
        settings,
        [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ],
        temperature=settings.temperature,
        sleep=sleep,
    )
    duration = time.monotonic() - started

    package = extract_json(content)

    return AnalyzeResult(
        package=package,
        usage=usage,
        prompt_sha256=hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        truncated=truncated,
        attempts=attempts,
        duration_seconds=duration,
    )


def suggest_fixes(
    flow_xml: str,
    scripts: dict[str, str],
    test_output: str,
    *,
    context: str = "",
    settings: Settings | None = None,
    client: Any | None = None,
    sleep=time.sleep,
    max_output_chars: int = 20_000,
) -> str:
    """
    Analyse a failure log and suggest fixes in the iFlow and its scripts.

    The log may come from the generated pytest suite or from the Groovy harness.
    Both matter: when only the harness fails, the useful evidence is its output,
    which names the real script and the assertion that did not hold.

    `context` is a preamble placed BEFORE the logs and outside the truncation window.
    It explains how the harness executes a script, which the model cannot infer from
    the logs alone. It is kept separate on purpose: the logs are truncated from the
    front, so a preamble concatenated into them would be the first thing discarded
    precisely when the log is long - the case where the model most needs it.
    """
    settings = settings or Settings.from_env()
    system_prompt = (
        "You are a senior SAP integration engineer. The tests generated for the SAP "
        "Integration Flow have failed. Analyze the failure logs alongside the original "
        "flow. Provide concrete suggestions on what needs to be fixed in the SAP IS XML or "
        "the Groovy scripts. "
        "Identify which layer a failure belongs to - the iFlow, a Groovy script, the "
        "generated test, or the test harness itself - and say so explicitly. Do not "
        "propose changes for a failure caused by the harness or by missing test data. "
        f"Answer in the language corresponding to the ISO code {settings.language_code}."
    )

    prompt = (
        f"{context}\n\n" if context.strip() else ""
    ) + (
        "--- SAP IFLOW XML ---\n"
        f"{flow_xml[:20_000]}\n\n"
        "--- GROOVY SCRIPTS ---\n"
        f"{json.dumps(scripts, ensure_ascii=False)[:10_000]}\n\n"
        "--- FAILURE LOGS ---\n"
        f"{test_output[-max_output_chars:]}\n\n"
        "Based on the failure logs, what is misconfigured in the SAP iflow or scripts? "
        "How do we fix it? If the evidence points at the harness, the generated test or "
        "missing fixtures rather than the artifact, say that instead of proposing an edit."
    )

    resolved_client = create_client(settings, client=client)
    content, _usage, _attempts = _chat_completion(
        resolved_client,
        settings,
        [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ],
        temperature=0.3,
        sleep=sleep,
    )
    return content
