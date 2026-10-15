"""
extract.py - locate and read iFlow artifacts from repository directories.

Motivation (blocker B1'): the original `extract_iflw()` only knows how to read `.zip`
(`zipfile.ZipFile`). In the new design the iFlow reaches the repository through the
native `Git Push` of SAP Integration Suite, which writes the files **exploded**. If the
pipeline keeps requiring `.zip`, it breaks on the first real run.

Layout observed in a package exported from SAP IS (and therefore expected from Git Push):

    <root>/
      .project
      metainfo.prop
      META-INF/MANIFEST.MF
      src/main/resources/
        scenarioflows/integrationflow/<Name>.iflw
        script/*.groovy  *.gsh
        mapping/*.mmap
        wsdl/*.wsdl
        parameters.prop / parameters.propdef

The extractor is **layout-agnostic**: it does not assume those names. It looks for
any `*.iflw` (or an IntegrationFlow XML) under the given root and collects the sibling
scripts by walking the tree, exactly as section 5.1 of the analysis requires.
"""

from __future__ import annotations

import re
import zipfile
from pathlib import Path
from typing import Iterable

# Extensions treated as artifact "script": they are sent to the LLM along with the XML.
SCRIPT_SUFFIXES = (".groovy", ".xslt", ".js", ".gsh")

# Extension of the main artifact.
IFLOW_SUFFIX = ".iflw"

# Directories whose content must never be considered an iFlow artifact. The comparison
# is made against the path *relative to the given root*, never against the absolute
# path - otherwise a root such as "...\export\MyFlow" would exclude itself.
EXCLUDED_DIR_NAMES = {
    ".git",
    ".github",
    "__pycache__",
    ".pytest_cache",
    "node_modules",
    "stubs_classes",
    "tests",
    "test-plans",
    "reports",
    "generated",
    "legacy",
    "harness",
    "stubs",
}

# Cap per script. A script larger than this is truncated with a warning, instead of
# silently consuming memory and LLM quota.
MAX_SCRIPT_BYTES = 512 * 1024


class ExtractionError(Exception):
    """Failure to locate or read the artifacts of an iFlow."""


def _iter_candidate_files(root: Path) -> Iterable[Path]:
    """
    Walks the tree ignoring irrelevant directories and symbolic links.

    Exclusion considers only the components **relative** to `root`.
    """
    root = Path(root)
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            continue
        try:
            relative_parts = path.relative_to(root).parts
        except ValueError:  # pragma: no cover - rglob always yields children
            continue
        if any(part in EXCLUDED_DIR_NAMES for part in relative_parts):
            continue
        if path.is_file():
            yield path


def find_iflow_files(root: Path) -> list[Path]:
    """
    Locates the iFlow definition files under `root`.

    Accepts `*.iflw` and, as a fallback, an `.xml` that looks like an
    IntegrationFlow (name containing "integrationflow", case-insensitive).
    """
    root = Path(root)
    if not root.exists():
        raise ExtractionError(f"Directory not found: {root}")
    if not root.is_dir():
        raise ExtractionError(f"Not a directory: {root}")

    iflw: list[Path] = []
    xml_candidates: list[Path] = []

    for path in _iter_candidate_files(root):
        suffix = path.suffix.lower()
        if suffix == IFLOW_SUFFIX:
            iflw.append(path)
        elif suffix == ".xml" and "integrationflow" in path.as_posix().lower():
            xml_candidates.append(path)

    # Always prefer `.iflw`; fall back to XML only when there is none.
    found = iflw or xml_candidates
    return sorted(found)


def collect_scripts(root: Path) -> dict[str, str]:
    """
    Collects the artifact scripts, recursively, as {relative_path: content}.

    The key is the path relative to the artifact root, so that the LLM can
    correlate each script with its role inside the iFlow.
    """
    root = Path(root)
    scripts: dict[str, str] = {}

    for path in _iter_candidate_files(root):
        if path.suffix.lower() not in SCRIPT_SUFFIXES:
            continue
        relative = path.relative_to(root).as_posix()
        try:
            size = path.stat().st_size
            if size > MAX_SCRIPT_BYTES:
                print(
                    f"[WARNING] Script '{relative}' has {size} bytes "
                    f"(> {MAX_SCRIPT_BYTES}); content truncated."
                )
                with path.open(encoding="utf-8", errors="replace") as handle:
                    scripts[relative] = handle.read(MAX_SCRIPT_BYTES)
                continue
            scripts[relative] = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:  # an unreadable file must not break the extraction
            scripts[relative] = f"<<ERROR READING FILE: {exc}>>"

    return scripts


def _collect_normalized_scripts(iflow_file: Path) -> dict[str, str]:
    """
    Collects the artifact scripts starting from the `.iflw`, with the keys already
    normalized (without the `src/main/resources/` prefix).

    Collecting only the parent directory of the `.iflw` is not enough: in the SAP IS
    layout the `.iflw` sits in `.../scenarioflows/integrationflow/` and the scripts in
    `.../script/`, which is a **sibling** directory. That is why we start from the
    artifact root.
    """
    scripts = collect_scripts(artifact_root_for(iflow_file))
    return {_strip_package_prefix(name): content for name, content in scripts.items()}


def extract_from_dir(root: str | Path) -> tuple[str, dict[str, str], Path]:
    """
    Extracts the iFlow XML and the scripts from a repository directory.

    Returns `(flow_xml, scripts, iflow_file)`.

    Unlike the original `extract_iflw()`, this function does not depend on `.zip` and
    does not assume fixed directory names.
    """
    root = Path(root)
    iflow_files = find_iflow_files(root)

    if not iflow_files:
        raise ExtractionError(
            f"No .iflw file found in {root}. "
            "Check that the directory points to the root of the iFlow artifact."
        )

    iflow_file = iflow_files[0]
    if len(iflow_files) > 1:
        # Not an error, but the caller must know that a choice was made.
        others = ", ".join(p.name for p in iflow_files[1:])
        print(
            f"[WARNING] {len(iflow_files)} iFlow files found in {root}; "
            f"using '{iflow_file.name}' and ignoring: {others}"
        )

    try:
        flow_xml = iflow_file.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise ExtractionError(f"Failed to read {iflow_file}: {exc}") from exc

    scripts = _collect_normalized_scripts(iflow_file)

    if not scripts:
        print(
            f"[WARNING] No script (.groovy/.xslt/.js/.gsh) found next to "
            f"{iflow_file.name}; the LLM will analyze only the XML."
        )

    return flow_xml, scripts, iflow_file


def artifact_root_for(iflow_file: Path) -> Path:
    """
    Determines the artifact root from the `.iflw` path.

    Rule: if the path contains the sequence `src/main/resources`, the artifact
    root is the directory that precedes `src` (the full package, which also
    contains `META-INF` and `metainfo.prop`). Otherwise, it uses the parent
    directory of the `.iflw` - enough for a flat layout per artifact.
    """
    iflow_file = Path(iflow_file)
    parts = iflow_file.parts

    for index in range(len(parts) - 2):
        if parts[index : index + 3] == ("src", "main", "resources"):
            return Path(*parts[:index]) if index > 0 else Path(".")

    return iflow_file.parent


# Internal prefix of the package exported by SAP IS. Removed from the script keys
# so that the LLM sees short and meaningful paths
# ("script/MyScript.groovy") instead of "src/main/resources/script/...".
_PACKAGE_PREFIX = ("src", "main", "resources")


def _strip_package_prefix(relative: str) -> str:
    """Removes the `src/main/resources/` prefix from a relative path, if present."""
    parts = Path(relative).parts
    if parts[:3] == _PACKAGE_PREFIX:
        return Path(*parts[3:]).as_posix()
    return Path(relative).as_posix()



def extract_from_zip(file_path: str | Path) -> tuple[str, dict[str, str]]:
    """
    Reads a `.zip` package exported from SAP IS.

    Kept only for local/legacy use; the CI path uses `extract_from_dir()`.
    Same semantics as the original `extract_iflw()`.
    """
    file_path = Path(file_path)
    flow_xml: str | None = None
    scripts: dict[str, str] = {}

    with zipfile.ZipFile(file_path) as archive:
        for name in archive.namelist():
            lower = name.lower()
            if lower.endswith(IFLOW_SUFFIX) and "integrationflow" in lower:
                flow_xml = archive.read(name).decode("utf-8", errors="replace")
            elif lower.endswith(".xml") and "IntegrationFlow" in name:
                flow_xml = archive.read(name).decode("utf-8", errors="replace")
            elif lower.endswith(SCRIPT_SUFFIXES):
                scripts[name] = archive.read(name).decode("utf-8", errors="replace")

    if flow_xml is None:
        raise ExtractionError(f"No IntegrationFlow XML found in {file_path.name}.")

    return flow_xml, scripts


def extract(source: str | Path) -> tuple[str, dict[str, str], Path | None]:
    """
    Single entry point: decides between directory (CI) and `.zip` (local).

    Returns `(flow_xml, scripts, iflow_file)`, with `iflow_file` being `None`
    when the source was a `.zip`.
    """
    source = Path(source)

    if source.is_dir():
        return extract_from_dir(source)

    if source.is_file() and source.suffix.lower() == ".zip":
        flow_xml, scripts = extract_from_zip(source)
        return flow_xml, scripts, None

    if source.is_file() and source.suffix.lower() == IFLOW_SUFFIX:
        flow_xml = source.read_text(encoding="utf-8", errors="replace")
        # Same normalization as the directory path: the scripts may sit in a
        # sibling directory (SAP IS layout), not only next to the .iflw.
        return flow_xml, _collect_normalized_scripts(source), source

    raise ExtractionError(
        f"Invalid source: {source}. Provide an artifact directory, an "
        ".iflw file or a .zip package."
    )


# ---------------------------------------------------------------------------
# Secret cleanup (before any submission to the LLM)
# ---------------------------------------------------------------------------

SECRET_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(
            r"(?i)(password|passwd|client_secret|privatekey|secret)\s*[=:]\s*[\"']?[^\"',\s}]+"
        ),
        r'\1="[REDACTED]"',
    ),
    (re.compile(r"(?i)(<password>).*?(</password>)"), r"\1[REDACTED]\2"),
    (re.compile(r"(?i)(<clientSecret>).*?(</clientSecret>)"), r"\1[REDACTED]\2"),
    # Added with respect to the original pipeline: credentials that appear
    # in iFlow artifacts and were not covered.
    (re.compile(r"(?i)(<user>).*?(</user>)"), r"\1[REDACTED]\2"),
    (re.compile(r"(?i)(oauth_?token|access_?token|api_?key)\s*[=:]\s*[\"']?[^\"',\s}]+"), r"\1=[REDACTED]"),
    (re.compile(r"(?i)Basic\s+[A-Za-z0-9+/=]{12,}"), "Basic [REDACTED]"),
]


def scrub(text: str) -> str:
    """
    Removes credentials and secrets from the XML/scripts before sending to the LLM.

    The `.iflw` is an untrusted input: it may contain credentials of
    third-party systems that must not leave to an external service.
    """
    for pattern, replacement in SECRET_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


# ---------------------------------------------------------------------------
# Reachability
# ---------------------------------------------------------------------------

_BPMN_NODE = re.compile(
    r"<bpmn2:(startEvent|endEvent|callActivity|serviceTask|scriptTask|exclusiveGateway"
    r"|parallelGateway|inclusiveGateway|intermediateCatchEvent|subProcess|task)"
    r"\b([^>]*)>"
)


def find_unreachable(flow_xml: str) -> list[dict[str, str]]:
    """
    BPMN elements wired to nothing, derived from the artifact.

    An element with no incoming sequence flow cannot be reached, except a start event;
    one with no outgoing flow terminates, except an end event. Either way the element is
    not part of the executing process.

    This is computed rather than left to the model. `coverage.json` reported such
    elements in one generation and omitted them in the next, so a real 0 % entry came and
    went with the weather. Counting the sequence flows is deterministic: the same
    artifact always produces the same list.

    Returns `[{"id": ..., "name": ..., "element": ...}, ...]`, sorted by id.
    """
    found: dict[str, dict[str, str]] = {}

    for match in _BPMN_NODE.finditer(flow_xml):
        tag, attrs = match.group(1), match.group(2)
        ident = re.search(r'id="([^"]+)"', attrs)
        if not ident:
            continue

        # Connectivity lives inside the element, up to its closing tag.
        body = flow_xml[match.end() : match.end() + 4000]
        close = body.find(f"</bpmn2:{tag}>")
        if close != -1:
            body = body[:close]

        incoming = re.findall(r"<bpmn2:incoming>([^<]+)</bpmn2:incoming>", body)
        outgoing = re.findall(r"<bpmn2:outgoing>([^<]+)</bpmn2:outgoing>", body)

        unreachable = (not incoming and tag != "startEvent") or (
            not outgoing and tag != "endEvent"
        )
        if not unreachable:
            continue

        name = re.search(r'name="([^"]*)"', attrs)
        found[ident.group(1)] = {
            "id": ident.group(1),
            "name": (name.group(1) if name else "").strip(),
            "element": tag,
        }

    return [found[key] for key in sorted(found)]


def merge_unreachable(coverage, flow_xml: str) -> dict:
    """
    Fold the reachability finding into the coverage report.

    The unreachable elements are added at 0 % and named in `uncovered_paths`, so the
    dashboard shows a bar that is visibly not green and the suggested fixes can address
    them. Anything the model already reported is preserved.
    """
    report = dict(coverage) if isinstance(coverage, dict) else {}
    unreachable = find_unreachable(flow_xml)
    if not unreachable:
        return report

    by_component = dict(report.get("coverage_by_component") or {})
    for item in unreachable:
        label = item["name"] or item["element"]
        by_component[f"{item['id']} ({label})"] = "0%"
    report["coverage_by_component"] = by_component

    described = "; ".join(
        f"{item['id']} ({item['name'] or item['element']})" for item in unreachable
    )
    existing = list(report.get("uncovered_paths") or [])
    note = (
        f"Unreachable in the process: {described}. These elements have no sequence flow "
        "connecting them to the flow, so no test can exercise them."
    )
    if not any("Unreachable in the process" in str(entry) for entry in existing):
        existing.append(note)
    report["uncovered_paths"] = existing

    return report
