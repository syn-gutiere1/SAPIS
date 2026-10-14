"""
pipeline.py - SAP Integration Suite AI-assisted test generation pipeline.

Configuration is loaded from a .env file:
  PORTKEY_BASE_URL    - Portkey gateway base URL (e.g. https://api.portkey.ai/v1)
  PORTKEY_AUTH_TOKEN  - authentication token (virtual key or account key) [REQUIRED]
  LLM_MODEL           - model name exposed by your Portkey virtual key [REQUIRED]
  AI_PROMPT_FILE      - path to a text file with the prompt template (multi-line safe)
  AI_PROMPT           - optional single-line prompt template (fallback if no file)

Usage:
  python pipeline.py                    # scans export/ and processes all .iflw/.zip
  python pipeline.py export\flow.iflw   # processes a single file explicitly

Each flow generates its output in generated/<flow_name>/
"""

import json
import os
import re
import sys
import zipfile
from pathlib import Path

from openai import OpenAI

# ---------------------------------------------------------------------------
# .env loader (uses python-dotenv if available, otherwise a minimal parser)
# ---------------------------------------------------------------------------
try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:

    def _load_env(path=".env"):
        if not Path(path).exists():
            return
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))

    _load_env()

# ---------------------------------------------------------------------------
# Configuration from .env
# ---------------------------------------------------------------------------
PORTKEY_BASE_URL = os.environ.get("PORTKEY_BASE_URL", "https://api.portkey.ai/v1")
if not PORTKEY_BASE_URL.endswith("/v1"):
    PORTKEY_BASE_URL = f"{PORTKEY_BASE_URL}/v1"

PORTKEY_AUTH_TOKEN = os.environ.get("PORTKEY_AUTH_TOKEN", "")
LLM_MODEL = os.environ.get("LLM_MODEL", "")
LANGUAGE_CODE = os.environ.get("LANGUAGE_CODE", "en_US")

AI_PROMPT = ""
_client = None
_runtime_ready = False


class PipelineRuntimeError(Exception):
    """Raised when pipeline runtime prerequisites are not satisfied."""


# ---------------------------------------------------------------------------
# Runtime readiness
# ---------------------------------------------------------------------------
def _validate_env_errors():
    errors = []
    if not PORTKEY_AUTH_TOKEN:
        errors.append("PORTKEY_AUTH_TOKEN is missing in .env")
    if not LLM_MODEL:
        errors.append("LLM_MODEL is missing in .env")
    if not PORTKEY_BASE_URL:
        errors.append("PORTKEY_BASE_URL is missing in .env")
    return errors


def _load_prompt_template():
    prompt_file = os.environ.get("AI_PROMPT_FILE", "prompt_template.txt")
    prompt_path = Path(prompt_file)

    if prompt_path.exists():
        prompt = prompt_path.read_text(encoding="utf-8")
        print(f"Prompt loaded from {prompt_file} ({len(prompt)} chars)")
        return prompt

    inline_prompt = os.environ.get("AI_PROMPT")
    if inline_prompt:
        print("Prompt loaded from AI_PROMPT env var")
        return inline_prompt

    raise PipelineRuntimeError(
        f"Prompt template file '{prompt_file}' not found. "
        "Create it or set AI_PROMPT_FILE in .env."
    )


def ensure_runtime_ready():
    """Initialize and validate LLM runtime lazily."""
    global _client, _runtime_ready, AI_PROMPT

    if _runtime_ready:
        return

    errors = _validate_env_errors()
    if errors:
        raise PipelineRuntimeError("Invalid .env configuration:\n  - " + "\n  - ".join(errors))

    AI_PROMPT = _load_prompt_template()
    if "{{flow_xml}}" not in AI_PROMPT or "{{scripts}}" not in AI_PROMPT:
        print("[WARNING] Prompt template is missing {{flow_xml}} or {{scripts}} placeholders.")

    _client = OpenAI(
        api_key=PORTKEY_AUTH_TOKEN,
        base_url=PORTKEY_BASE_URL,
    )
    print(f"Config OK: base={PORTKEY_BASE_URL} model={LLM_MODEL}")
    _runtime_ready = True


# ---------------------------------------------------------------------------
# 1. Artifact extraction
# ---------------------------------------------------------------------------
def extract_iflw(file_path):
    """Extract flow XML and scripts from an .iflw or package .zip exported from SAP IS."""
    file_path = Path(file_path)
    flow_xml, scripts = None, {}
    with zipfile.ZipFile(file_path) as z:
        for name in z.namelist():
            if name.endswith(".iflw") and "integrationflow" in name.lower():
                flow_xml = z.read(name).decode("utf-8")
            elif name.endswith(".xml") and "IntegrationFlow" in name:
                flow_xml = z.read(name).decode("utf-8")
            elif name.endswith((".groovy", ".js", ".xslt")):
                scripts[name] = z.read(name).decode("utf-8")
    if flow_xml is None:
        raise ValueError(f"No IntegrationFlow XML found in {file_path.name}.")
    return flow_xml, scripts


def extract_flow_xml(file_path):
    """Accepts .iflw/.zip OR a plain .xml copied from the editor."""
    file_path = Path(file_path)
    if file_path.suffix.lower() in (".iflw", ".zip"):
        return extract_iflw(file_path)
    return file_path.read_text(encoding="utf-8"), {}


# ---------------------------------------------------------------------------
# 2. Secret scrubbing (before sending anything to the LLM)
# ---------------------------------------------------------------------------
SECRET_PATTERNS = [
    (
        re.compile(r"(?i)(password|passwd|client_secret|privatekey|secret)\s*[=:]\s*[\"\']?[^\"\',\s}]+"),
        r'\1="[REDACTED]"',
    ),
    (re.compile(r"(?i)(<password>).*?(</password>)"), r"\1[REDACTED]\2"),
    (re.compile(r"(?i)(<clientSecret>).*?(</clientSecret>)"), r"\1[REDACTED]\2"),
]


def scrub(text):
    """Remove credentials and secrets from flow XML / scripts before LLM analysis."""
    for pattern, repl in SECRET_PATTERNS:
        text = pattern.sub(repl, text)
    return text


# ---------------------------------------------------------------------------
# 3. Portkey LLM client + prompt
# ---------------------------------------------------------------------------
def build_prompt(flow_xml, scripts):
    """Fill the prompt template using replace() to avoid brace conflicts."""
    return AI_PROMPT.replace("{{flow_xml}}", flow_xml[:30000]).replace(
        "{{scripts}}", json.dumps(scripts, ensure_ascii=False)[:30000]
    )


def analyze_with_ai(flow_xml, scripts):
    """Send scrubbed artifacts to the LLM and return the parsed JSON test package."""
    ensure_runtime_ready()

    system_prompt = (
        "You are a senior SAP integration engineer. Always answer with valid JSON. "
        "Write all your response content, descriptions, comments, risk explanations, "
        f"and test case names in the language corresponding to the ISO code {LANGUAGE_CODE}."
    )

    resp = _client.chat.completions.create(
        model=LLM_MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": build_prompt(flow_xml, scripts)},
        ],
        temperature=0.2,
    )

    content = resp.choices[0].message.content

    if "```json" in content:
        content = content.split("```json")[1].split("```")[0].strip()
    elif "```" in content:
        content = content.split("```")[1].split("```")[0].strip()

    try:
        return json.loads(content)
    except json.JSONDecodeError as e:
        raise PipelineRuntimeError(
            f"Failed to parse LLM response as JSON: {e}\n"
            f"--- RAW RESPONSE CONTENT ---\n{content}\n"
            "----------------------------------"
        ) from e


# ---------------------------------------------------------------------------
# 4. Write generated artifacts to disk
# ---------------------------------------------------------------------------
def find_key(data, keywords):
    """Search for keys in JSON flexibly, since LLM might change key names."""
    for k, v in data.items():
        k_lower = k.lower()
        if any(kw in k_lower for kw in keywords):
            return v
    return None


def write_test_package(package, out_dir="generated"):
    """Persist scenarios, test data and pytest code produced by the LLM."""
    out_dir = Path(out_dir)
    (out_dir / "testdata").mkdir(parents=True, exist_ok=True)

    scenarios = find_key(package, ["scenario", "scenarios"]) or []
    (out_dir / "cases.json").write_text(
        json.dumps(scenarios, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    test_data = find_key(package, ["test_data", "test data", "testdata"])
    if isinstance(test_data, dict):
        test_data = [test_data]
    elif not test_data:
        test_data = []

    for item in test_data:
        if isinstance(item, dict) and "file" in item and "content" in item:
            (out_dir / item["file"]).write_text(item["content"], encoding="utf-8")
        elif isinstance(item, dict) and "filename" in item and "content" in item:
            (out_dir / item["filename"]).write_text(item["content"], encoding="utf-8")

    pytest_code = find_key(package, ["pytest", "script"]) or ""
    if isinstance(pytest_code, list):
        pytest_code = "\n".join(pytest_code)
    elif isinstance(pytest_code, dict):
        if "content" in pytest_code:
            pytest_code = pytest_code["content"]
        elif "test_flow.py" in pytest_code:
            pytest_code = pytest_code["test_flow.py"]
        else:
            pytest_code = "\n".join(str(v) for v in pytest_code.values())

    if pytest_code:
        (out_dir / "test_flow.py").write_text(pytest_code, encoding="utf-8")

    risks = find_key(package, ["risk", "risks"]) or []
    (out_dir / "risks.json").write_text(
        json.dumps(risks, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    (out_dir / "raw_llm_output.json").write_text(
        json.dumps(package, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    return out_dir


def suggest_fixes_with_ai(flow_xml, scripts, test_output):
    """If tests fail, analyze logs with LLM and suggest SAP IS fixes."""
    ensure_runtime_ready()

    system_prompt = (
        "You are a senior SAP integration engineer. The pytest script generated for the SAP "
        "Integration Flow has failed. Analyze the pytest failure logs alongside the original "
        "flow. Provide concrete suggestions on what needs to be fixed in the SAP IS XML or "
        "Groovy scripts. "
        f"Answer in the language corresponding to the ISO code {LANGUAGE_CODE}."
    )

    prompt = (
        "--- SAP IFLOW XML ---\n"
        f"{flow_xml[:20000]}\n\n"
        "--- GROOVY SCRIPTS ---\n"
        f"{json.dumps(scripts, ensure_ascii=False)[:10000]}\n\n"
        "--- PYTEST FAILURE LOGS ---\n"
        f"{test_output}\n\n"
        "Based on the failure logs, what is misconfigured in the SAP iflow or scripts? How do we fix it?"
    )

    print("\n[AI] Analyzing failure logs with LLM to suggest fixes...")
    resp = _client.chat.completions.create(
        model=LLM_MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ],
        temperature=0.3,
    )
    return resp.choices[0].message.content


# ---------------------------------------------------------------------------
# 5. Programmatic API (used by Streamlit)
# ---------------------------------------------------------------------------
def discover_export_files(export_dir="export", recursive=False, suffixes=(".iflw", ".zip", ".xml")):
    """Find candidate iFlow files inside export/ (optionally recursive)."""
    base = Path(export_dir)
    if not base.exists():
        return []

    normalized = tuple(s.lower() for s in suffixes)
    if recursive:
        files = [p for p in base.rglob("*") if p.is_file() and p.suffix.lower() in normalized]
    else:
        files = [p for p in base.iterdir() if p.is_file() and p.suffix.lower() in normalized]

    return sorted(files)


def process_files(files, generated_root="generated"):
    """Process a list of files and return a summary with ok/fail entries."""
    ensure_runtime_ready()

    generated_root = Path(generated_root)
    summary = {"ok": [], "fail": []}

    for file_path in files:
        file_path = Path(file_path)
        print(f"\n=== Processing {file_path.name} ===")

        try:
            flow_xml, scripts = extract_flow_xml(file_path)
        except (zipfile.BadZipFile, ValueError, OSError) as e:
            msg = (
                f"{e}. Ensure the file is a valid .iflw/.zip XML export "
                "(not an HTML login page or corrupted archive)."
            )
            summary["fail"].append({"file": str(file_path), "error": msg})
            print(f"  [ERROR] {msg}")
            continue

        try:
            print(f"XML: {len(flow_xml)} chars | scripts: {len(scripts)}")
            flow_xml = scrub(flow_xml)
            scripts = {name: scrub(content) for name, content in scripts.items()}

            package = analyze_with_ai(flow_xml, scripts)
            out_dir = write_test_package(package, out_dir=generated_root / file_path.stem)

            scenarios = find_key(package, ["scenario", "scenarios"]) or []
            risks = find_key(package, ["risk", "risks"]) or []

            summary["ok"].append(
                {
                    "file": str(file_path),
                    "out_dir": str(out_dir),
                    "scenarios": len(scenarios) if isinstance(scenarios, list) else 0,
                    "risks": len(risks) if isinstance(risks, list) else 0,
                }
            )
            print(f"Test package saved in {out_dir}/")
        except Exception as e:
            summary["fail"].append({"file": str(file_path), "error": str(e)})
            print(f"  [ERROR] {e}")

    return summary


# ---------------------------------------------------------------------------
# 6. Main - manual mode only
# ---------------------------------------------------------------------------
def main():
    # 1) File passed explicitly as argument
    if len(sys.argv) > 1 and Path(sys.argv[1]).exists():
        files = [Path(sys.argv[1])]

    # 2) Auto-scan of the export/ folder (top-level only for backward compatibility)
    else:
        files = discover_export_files("export", recursive=False, suffixes=(".iflw", ".zip"))

        if not files:
            sys.exit(
                "No .iflw/.zip file found in export/. "
                "Place the exported files in the export/ folder or pass a file: "
                "python pipeline.py export\\flow.iflw"
            )

        print(f"Auto mode: {len(files)} file(s) found in export/:")
        for f in files:
            print(f"  - {f.name}")

    try:
        result = process_files(files, generated_root="generated")
    except PipelineRuntimeError as e:
        sys.exit(f"[ERROR] {e}")

    print("\n=== Summary ===")
    print(f"Success: {len(result['ok'])}")
    print(f"Failed: {len(result['fail'])}")

    if result["fail"]:
        print("Failures:")
        for item in result["fail"]:
            print(f"  - {item['file']}: {item['error']}")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--analyze-failures":
        if len(sys.argv) < 4:
            sys.exit("Usage: python pipeline.py --analyze-failures <file_path_zip> <test_logs_file>")

        file_path = Path(sys.argv[2])
        logs_path = Path(sys.argv[3])

        if not file_path.exists() or not logs_path.exists():
            sys.exit("[ERROR] Files not found for analysis.")

        try:
            ensure_runtime_ready()
            flow_xml, scripts = extract_flow_xml(file_path)
            test_output = logs_path.read_text(encoding="utf-8", errors="replace")

            suggestion = suggest_fixes_with_ai(
                scrub(flow_xml),
                {name: scrub(content) for name, content in scripts.items()},
                test_output,
            )
        except (PipelineRuntimeError, Exception) as e:
            sys.exit(f"[ERROR] {e}")

        out_dir = Path("generated") / file_path.stem
        out_dir.mkdir(parents=True, exist_ok=True)
        suggestion_file = out_dir / "ai_fix_suggestions.md"
        suggestion_file.write_text(suggestion, encoding="utf-8")
        print(f"\n[SUCCESS] AI suggestions saved in: {suggestion_file}")
    else:
        main()
