#!/usr/bin/env python3
"""
cli.py - command line interface for the pipeline.

Replaces the Windows `.bat` automation (`run_tests.bat`, with `pause`,
`cd ..\\..` and `errorlevel`), which does not run on a Linux runner.

Stable exit codes (contract with the workflow):

    0  success
    2  invalid configuration or usage
    3  failure in the call to the LLM gateway
    4  tests failed

Modes:

    extract   reads the artifact and writes a diagnostic summary
    generate  reads the artifact, calls the LLM and writes the test artifacts
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

# Package imports are resolved inside main(), after making sure that
# `src/` is on sys.path. Without that, `python -m pipeline.cli` only works if the
# caller has exported PYTHONPATH - and the CLI has to be directly executable.

EXIT_OK = 0
EXIT_CONFIG = 2
EXIT_PORTKEY = 3
EXIT_TESTS = 4


def _ensure_src_on_path() -> None:
    """
    Makes sure the hub `src/` directory is on sys.path.

    Allows `python -m pipeline.cli` from the repository root without
    depending on an exported PYTHONPATH.
    """
    root = Path(__file__).resolve().parents[2]
    src = root / "src"
    if src.is_dir() and str(src) not in sys.path:
        sys.path.insert(0, str(src))
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))


def _modules():
    """
    Imports the package modules after adjusting sys.path.

    Returns `(extract, portkey, writer)`. The tests access
    `cli.portkey_mod`, so the names stay assigned at module level on the
    first call.
    """
    global extract_mod, portkey_mod, writer_mod

    if extract_mod is None:
        _ensure_src_on_path()
        from . import extract as _extract
        from . import portkey as _portkey
        from . import writer as _writer

        extract_mod, portkey_mod, writer_mod = _extract, _portkey, _writer

    return extract_mod, portkey_mod, writer_mod


extract_mod = None
portkey_mod = None
writer_mod = None


def _hash_mapping(mapping: dict[str, str]) -> dict[str, str]:
    """Individual hash of each script, for the provenance manifest."""
    return {
        name: hashlib.sha256(content.encode("utf-8")).hexdigest()
        for name, content in sorted(mapping.items())
    }


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_env_file(path: Path, *, override: bool = False) -> None:
    """
    Loads a local `.env`, without overwriting the environment by default.

    CI injects everything through the workflow environment and has no `.env` -
    this path exists only for local use. Variables already defined take
    precedence, so that the secret value is never replaced by a file.
    """
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if override or key not in os.environ:
            os.environ[key] = value


def _build_manifest(
    *,
    iflow_file: Path | None,
    source: Path,
    out_dir: Path,
    plans_dir: Path,
    result: Any,
    write_summary: dict,
    settings: Any,
    scripts: dict[str, str],
) -> dict:
    """Builds the provenance manifest: links test -> commit -> model/prompt."""
    iflow_name = iflow_file.stem if iflow_file else source.stem

    manifest = {
        "iflow": iflow_name,
        "source": str(source),
        "source_relative": (
            iflow_file.relative_to(source).as_posix()
            if iflow_file is not None and iflow_file.is_relative_to(source)
            else (iflow_file.name if iflow_file else None)
        ),
        "iflow_sha256": _sha256_file(iflow_file) if iflow_file else None,
        "scripts_sha256": _hash_mapping(scripts),
        "scripts_count": len(scripts),
        "outputs": {
            "tests": str(out_dir),
            "plans": str(plans_dir),
        },
        "artifacts": {
            "scenarios": write_summary["scenarios"],
            "risks": write_summary["risks"],
            "testdata_files": write_summary["testdata_files"],
            "files": write_summary["files"],
            "rejected": write_summary["rejected"],
        },
        "llm": {
            "model": result.usage.model or settings.model,
            "base_url": settings.base_url,
            "temperature": settings.temperature,
            "language_code": settings.language_code,
            **result.usage.as_dict(),
        },
        "prompt": {
            "file": str(settings.prompt_file),
            "sha256": result.prompt_sha256,
        },
        "truncated": result.truncated,
        "attempts": result.attempts,
        "duration_seconds": round(result.duration_seconds, 3),
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "environment": {
            "github_run_id": os.environ.get("GITHUB_RUN_ID"),
            "github_sha": os.environ.get("GITHUB_SHA"),
            "github_ref": os.environ.get("GITHUB_REF"),
            "github_repository": os.environ.get("GITHUB_REPOSITORY"),
        },
    }
    return manifest


def _copy_plan_artifacts(out_dir: Path, plans_dir: Path) -> list[str]:
    """
    Copies the plan artifacts (reviewable in a PR) to `test-plans/`.

    `cases.json`, `risks.json`, `coverage.json` and `raw_llm_output.json` stay
    versioned; the rest (fixtures and the suite) stays under `tests/`.
    """
    plans_dir.mkdir(parents=True, exist_ok=True)
    copied: list[str] = []

    for name in ("cases.json", "risks.json", "coverage.json"):
        origin = out_dir / name
        if origin.is_file():
            (plans_dir / name).write_text(origin.read_text(encoding="utf-8"), encoding="utf-8")
            copied.append(name)

    return copied


def _cmd_extract(args: argparse.Namespace) -> int:
    extract_mod, _portkey_mod, _writer_mod = _modules()

    source = Path(args.source)
    try:
        flow_xml, scripts, iflow_file = extract_mod.extract(source)
    except extract_mod.ExtractionError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return EXIT_CONFIG

    summary = {
        "source": str(source),
        "iflow": iflow_file.name if iflow_file else None,
        "flow_xml_chars": len(flow_xml),
        "scripts": {name: len(content) for name, content in sorted(scripts.items())},
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return EXIT_OK


def _cmd_generate(args: argparse.Namespace) -> int:
    extract_mod, portkey_mod, writer_mod = _modules()

    source = Path(args.source)
    out_dir = Path(args.out)
    plans_dir = Path(args.plans) if args.plans else out_dir

    # 1) Read the artifact from the repository
    try:
        flow_xml, scripts, iflow_file = extract_mod.extract(source)
    except extract_mod.ExtractionError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return EXIT_CONFIG

    # 2) Scrub secrets before they leave the machine
    flow_xml = extract_mod.scrub(flow_xml)
    scripts = {name: extract_mod.scrub(content) for name, content in scripts.items()}

    # 3) Configuration
    try:
        settings = portkey_mod.Settings.from_env()
    except portkey_mod.ConfigError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return EXIT_CONFIG

    # 4) Call the model
    print(
        f"[1/3] Artifact: {iflow_file.name if iflow_file else source} "
        f"({len(flow_xml)} chars of XML, {len(scripts)} script(s))"
    )
    try:
        result = portkey_mod.analyze(flow_xml, scripts, settings=settings)
    except portkey_mod.ConfigError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return EXIT_CONFIG
    except Exception as exc:  # noqa: BLE001 - gateway failure becomes exit 3
        print(f"[ERROR] Failure in the LLM call: {exc}", file=sys.stderr)
        return EXIT_PORTKEY

    print(f"      model={result.usage.model} attempts={result.attempts} "
          f"tokens={result.usage.total_tokens}")
    if result.truncated:
        print(f"[WARNING] Content truncated before sending: {', '.join(result.truncated)}")

    # 5) Write the artifacts. The XML is passed so the coverage report can include the
    # reachability of the BPMN elements, which is computed rather than asked of the model.
    try:
        write_summary = writer_mod.write_test_package(
            result.package, out_dir, flow_xml=flow_xml
        )
    except writer_mod.WriteError as exc:
        print(f"[ERROR] Failure writing the artifacts: {exc}", file=sys.stderr)
        return EXIT_CONFIG

    copied = _copy_plan_artifacts(out_dir, plans_dir)

    print(f"[2/3] Written to {out_dir}:")
    for name in write_summary["files"]:
        print(f"      {name}")
    for name in copied:
        print(f"      test-plans/{name}")
    for rejected in write_summary["rejected"]:
        print(f"[WARNING] Fixture discarded -> {rejected}", file=sys.stderr)

    if not (out_dir / "test_flow.py").is_file():
        print("[ERROR] The model did not produce test_flow.py; nothing to test.", file=sys.stderr)
        return EXIT_PORTKEY

    # 6) Provenance manifest
    manifest = _build_manifest(
        iflow_file=iflow_file,
        source=source,
        out_dir=out_dir,
        plans_dir=plans_dir,
        result=result,
        write_summary=write_summary,
        settings=settings,
        scripts=scripts,
    )

    if args.json_report:
        report_path = Path(args.json_report)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"[3/3] Manifest: {report_path}")

    return EXIT_OK


def _cmd_analyze_failures(args: argparse.Namespace) -> int:
    """
    Send the pytest log back to the model and write `ai_fix_suggestions.md`.

    This is the useful half of a failing run: the reports say *what* broke, and
    this says what is probably wrong in the iFlow. The function existed but had no
    caller, so the artifact was never produced.
    """
    extract_mod, portkey_mod, _writer_mod = _modules()

    # `--logs` is optional: with no failures there is nothing to diagnose, but the risks
    # still deserve the `Fix Risks` half, so the analysis runs on a green run too.
    test_output = ""
    if args.logs:
        logs_path = Path(args.logs)
        if not logs_path.is_file():
            print(f"[ERROR] failure log not found: {logs_path}", file=sys.stderr)
            return EXIT_CONFIG
        test_output = logs_path.read_text(encoding="utf-8", errors="replace")

    try:
        flow_xml, scripts, iflow_file = extract_mod.extract(Path(args.source))
    except extract_mod.ExtractionError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return EXIT_CONFIG

    try:
        settings = portkey_mod.Settings.from_env()
    except portkey_mod.ConfigError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return EXIT_CONFIG

    flow_xml = extract_mod.scrub(flow_xml)
    scripts = {name: extract_mod.scrub(content) for name, content in scripts.items()}

    # The preamble goes in its own slot so that truncating a long log cannot drop it.
    context = ""
    if args.context:
        context_path = Path(args.context)
        if context_path.is_file():
            context = context_path.read_text(encoding="utf-8", errors="replace")

    # The risks the plan already identified, so the answer can close with how to fix
    # or mitigate each one instead of only reacting to the failure.
    risks = None
    if args.risks:
        risks_path = Path(args.risks)
        if risks_path.is_file():
            try:
                risks = json.loads(risks_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                print(
                    f"[WARN] Could not parse {risks_path}; continuing without the risks.",
                    file=sys.stderr,
                )

    # The coverage report, which carries the uncovered paths and the components below
    # 100 %. Those deserve the same treatment as the risks: a concrete way to close them.
    coverage = None
    if args.coverage:
        coverage_path = Path(args.coverage)
        if coverage_path.is_file():
            try:
                coverage = json.loads(coverage_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                print(
                    f"[WARN] Could not parse {coverage_path}; continuing without it.",
                    file=sys.stderr,
                )

    iflow_name = iflow_file.stem if iflow_file else Path(args.source).stem
    n_riscos = len(risks) if isinstance(risks, list) else 0
    n_gaps = len(coverage.get("uncovered_paths") or []) if isinstance(coverage, dict) else 0
    print(
        f"[1/2] Analysing {iflow_name} "
        f"({len(test_output)} chars of log, {n_riscos} risk(s), {n_gaps} coverage gap(s))"
    )

    try:
        suggestion = portkey_mod.suggest_fixes(
            flow_xml,
            scripts,
            test_output,
            context=context,
            risks=risks,
            coverage=coverage,
            settings=settings,
        )
    except portkey_mod.ConfigError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return EXIT_CONFIG
    except Exception as exc:  # noqa: BLE001 - gateway failure maps to exit 3
        print(f"[ERROR] LLM call failed: {exc}", file=sys.stderr)
        return EXIT_PORTKEY

    destino = Path(args.suggestions) if args.suggestions else Path(args.out) / "ai_fix_suggestions.md"
    destino.parent.mkdir(parents=True, exist_ok=True)

    failou = bool(test_output.strip())
    origem = (
        "the failure log" if failou else "the flow, its scripts and the assessed risks"
    )
    cabecalho = (
        f"# Suggested fixes — {iflow_name}\n\n"
        f"Generated for commit `{os.environ.get('GITHUB_SHA', 'local')[:12]}` from "
        f"{origem}.\n\n"
        "> Written by a model from the iFlow XML and its scripts. It is a hypothesis to "
        "speed up the investigation, not a verdict: check it against the artifact before "
        "changing anything.\n\n"
        "---\n\n"
    )
    destino.write_text(cabecalho + suggestion + "\n", encoding="utf-8")
    print(f"[2/2] Written: {destino}")

    return EXIT_OK


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="pipeline",
        description="Automated test generator for SAP Integration Suite iFlows.",
    )
    parser.add_argument(
        "--mode",
        choices=("extract", "generate", "analyze-failures"),
        default="generate",
        help=(
            "extract: only reads and summarizes the artifact. "
            "generate: reads, calls the LLM and writes (default). "
            "analyze-failures: sends the pytest log to the LLM and writes ai_fix_suggestions.md."
        ),
    )
    parser.add_argument(
        "--source",
        required=True,
        help="Artifact directory in the repository, .iflw file or .zip package (local use).",
    )
    parser.add_argument("--out", default="tests", help="Destination for test_flow.py and testdata/.")
    parser.add_argument("--plans", default=None, help="Destination for cases/risks/coverage.json.")
    parser.add_argument("--json-report", default=None, help="Path to manifest.json.")
    parser.add_argument(
        "--logs",
        default=None,
        help=(
            "Failure log to analyse (analyze-failures mode). Optional: with no failures "
            "the analysis still runs, reviewing the flow and the risks instead of a log."
        ),
    )
    parser.add_argument(
        "--context",
        default=None,
        help=(
            "Preamble placed before the logs and OUTSIDE the truncation window "
            "(analyze-failures mode). Explains how the harness executes a script, which "
            "the model cannot infer from the logs alone."
        ),
    )
    parser.add_argument(
        "--risks",
        default=None,
        help=(
            "Path to risks.json (analyze-failures mode). Sent to the model so it can close "
            "the answer with a `Fix Risks` section."
        ),
    )
    parser.add_argument(
        "--coverage",
        default=None,
        help=(
            "Path to coverage.json (analyze-failures mode). Its uncovered paths and "
            "components below 100% are sent to the model, which closes with `Fix Coverage`."
        ),
    )
    parser.add_argument(
        "--suggestions",
        default=None,
        help="Where to write ai_fix_suggestions.md (analyze-failures mode).",
    )
    parser.add_argument(
        "--env-file",
        default=None,
        help="Local .env file (local use only; CI injects through the environment).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    if args.env_file:
        _load_env_file(Path(args.env_file))

    if args.mode == "extract":
        return _cmd_extract(args)
    if args.mode == "analyze-failures":
        return _cmd_analyze_failures(args)
    return _cmd_generate(args)


if __name__ == "__main__":
    sys.exit(main())
