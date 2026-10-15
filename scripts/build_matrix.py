#!/usr/bin/env python3
"""
build_matrix.py - discovers which iFlows changed in a commit and assembles the job matrix.

Replaces the v1 change detection (hash of the downloaded `.zip`): because the iFlow
reaches the repository through a native `Git Push`, Git itself already says what changed.

Typical use in the workflow:

    python scripts/build_matrix.py --before "${{ github.event.before }}" \\
                                   --after  "${{ github.sha }}" > matrix.json

Output: a JSON array ready for `fromJson()` in GitHub Actions.

    [
      {
        "package": "SI_Glovia_SalesOrderCreate",
        "iflow": "SI_Glovia_SalesOrderCreate",
        "path": ".",
        "artifact_root": "src/main/resources",
        "changed_files": ["src/main/resources/script/X.groovy"],
        "has_iflow_change": true
      }
    ]

Exit codes:
    0  success (including when nothing changed - the matrix comes out empty)
    2  invalid usage / environment without git / nonexistent root
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

# Special hash GitHub sends in `before` when there is no previous commit
# (new branch or first push).
NULL_SHA = "0" * 40

# Ignored patterns: they cost neither an LLM call nor a job.
# Any file whose path contains one of these directories is discarded.
IGNORED_DIRS = {
    ".git",
    ".github",
    "docs",
    "doc",
    "test-plans",
    "reports",
    "tests",
    "generated",
    "legacy",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    "stubs_classes",
}

# Suffixes that never represent a change in the iFlow artifact.
IGNORED_SUFFIXES = {".md", ".txt", ".rst", ".png", ".jpg", ".pdf", ".mmap"}

# Filename of the iFlow definition file.
IFLOW_SUFFIX = ".iflw"


class MatrixError(Exception):
    """Environment or usage failure that prevents assembling the matrix."""


# ---------------------------------------------------------------------------
# Git
# ---------------------------------------------------------------------------


def _run_git(args: list[str], repo: Path) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        raise MatrixError(
            f"git {' '.join(args)} failed (exit {result.returncode}): "
            f"{result.stderr.strip() or result.stdout.strip()}"
        )
    return result.stdout


def changed_files_git(before: str, after: str, repo: Path) -> list[str]:
    """Files changed between two commits, as POSIX relative paths."""
    output = _run_git(["diff", "--name-only", before, after], repo)
    return [line.strip() for line in output.splitlines() if line.strip()]


def tracked_iflow_files(repo: Path) -> list[str]:
    """All `.iflw` files tracked by Git - used in the full scan fallback."""
    output = _run_git(["ls-files"], repo)
    return [
        line.strip()
        for line in output.splitlines()
        if line.strip().lower().endswith(IFLOW_SUFFIX)
    ]


# ---------------------------------------------------------------------------
# Path classification
# ---------------------------------------------------------------------------


def is_ignored(relative_path: str) -> bool:
    """Decides whether a change is unrelated to the iFlow artifact."""
    path = Path(relative_path)
    if any(part in IGNORED_DIRS for part in path.parts):
        return True
    if path.suffix.lower() in IGNORED_SUFFIXES:
        return True
    return False


def artifact_root_for(relative_iflow_path: str) -> str:
    """
    Derives the artifact root from the relative path of the `.iflw`.

    SAP IS layout:  <root>/src/main/resources/scenarioflows/.../<Name>.iflw
    In that case the root is the directory preceding `src`.

    Flat layout:    <root>/<Name>.iflw
    In that case the root is the parent directory.
    """
    parts = Path(relative_iflow_path).parts

    for index in range(len(parts) - 2):
        if parts[index : index + 3] == ("src", "main", "resources"):
            if index > 0:
                return Path(*parts[:index]).as_posix()
            return "."

    parent = Path(relative_iflow_path).parent
    return parent.as_posix() if parent.parts else "."


def _find_artifact_iflow(relative_path: str, repo: Path) -> Path | None:
    """
    Locates the `.iflw` of the artifact a changed file belongs to.

    Takes the **relative** path of the changed file and returns the
    **absolute** path of the corresponding `.iflw` (or `None`).

    It walks up one directory at a time from the file's directory and looks for
    the `.iflw` under each one. The package exported by SAP IS has the scripts in
    a directory sibling to the `.iflw` (`.../script/` vs
    `.../scenarioflows/integrationflow/`), so the search must reach the common
    ancestor - the artifact root.
    """
    current = (repo / relative_path).parent.resolve()

    for candidate in [current, *current.parents]:
        if not candidate.is_relative_to(repo):
            return None
        if not candidate.exists():
            continue
        ifls = sorted(
            p
            for p in candidate.rglob(f"*{IFLOW_SUFFIX}")
            if not any(part in IGNORED_DIRS for part in p.relative_to(candidate).parts)
        )
        if ifls:
            return ifls[0]
    return None


def _package_name(artifact_root: str, repo_name: str) -> str:
    """Package name used in the output paths (tests/, test-plans/, reports/)."""
    if artifact_root in (".", ""):
        return repo_name
    return Path(artifact_root).name


def _package_prefix(iflow_relative: str) -> str:
    """
    The `src/main/resources`-style prefix an iFlow lives under, or `""`.

    A changed file belongs to an artifact when it sits under this prefix, not under
    the `.iflw`'s own directory: the `.iflw` lives in
    `.../scenarioflows/integrationflow/` while its scripts live in the sibling
    `.../script/`, so the common ancestor is the resources root.
    """
    parts = Path(iflow_relative).parts
    for index in range(len(parts) - 2):
        if parts[index : index + 3] == ("src", "main", "resources"):
            return Path(*parts[: index + 3]).as_posix()
    return ""


def enumerate_artifacts(repo: Path) -> list[tuple[str, str, str]]:
    """
    Every iFlow artifact in the repository, as `(path, artifact_root, iflow_relative)`.

    `path` is the package root used as the repository-relative source directory,
    `artifact_root` is the `.iflw` directory relative to that root, and
    `iflow_relative` is the `.iflw` path relative to the repository.
    """
    found: list[tuple[str, str, str]] = []

    for iflow in tracked_iflow_files(repo):
        root = artifact_root_for(iflow)
        if root == ".":
            artifact_dir = str(Path(iflow).parent.as_posix())
        else:
            artifact_dir = Path(iflow).relative_to(root).parent.as_posix()

        found.append(
            (
                root,
                artifact_dir if artifact_dir != "." else "",
                iflow,
            )
        )

    return found


def _assign_to_artifact(
    relative: str,
    repo: Path,
    artifacts: list[tuple[str, str, str]],
) -> str | None:
    """
    Return the `.iflw` whose package contains `relative`, or None.

    The match uses the package prefix (`src/main/resources` and above) rather than
    the `.iflw` directory, because the scripts live in a sibling folder. The
    **deepest** matching package wins, so an artifact nested inside another one is
    handled correctly.

    A file that is under no package - the pipeline's own code, a workflow,
    documentation - belongs to no iFlow and must not create a job for one. The
    previous implementation walked *up* from the changed file until it found any
    `*.iflw`, so in the single-repository layout every change at the root resolved
    to the first iFlow in the tree.
    """
    target = Path(relative)
    best: str | None = None
    best_depth = -1

    for _root, _artifact_dir, iflow_relative in artifacts:
        prefix = _package_prefix(iflow_relative)
        if not prefix:
            # Flat layout: the artifact is the `.iflw`'s own directory.
            prefix_dir = Path(iflow_relative).parent
        else:
            prefix_dir = Path(prefix)

        contains = prefix_dir in (Path("."), Path("")) or target.is_relative_to(prefix_dir)
        if not contains:
            continue

        depth = 0 if prefix_dir in (Path("."), Path("")) else len(prefix_dir.parts)
        if depth > best_depth:
            best, best_depth = iflow_relative, depth

    return best


# ---------------------------------------------------------------------------
# Matrix assembly
# ---------------------------------------------------------------------------


def build_matrix(
    changed: list[str],
    repo: Path,
    repo_name: str,
    force_full_scan: bool = False,
) -> list[dict]:
    """Assembles the job matrix from the list of changed files."""
    if force_full_scan:
        changed = tracked_iflow_files(repo)
    else:
        changed = [path for path in changed if not is_ignored(path)]

    known = enumerate_artifacts(repo)

    # 1) Discover the artifacts touched.
    artifact_roots: dict[str, dict] = {}

    for relative in changed:
        path = Path(relative)
        if path.suffix.lower() == IFLOW_SUFFIX:
            iflow_relative = relative
        else:
            iflow_relative = _assign_to_artifact(relative, repo, known)
            if iflow_relative is None:
                # Belongs to no iFlow artifact: the pipeline's own code, a
                # workflow, or documentation. It generates no job.
                continue

        root = artifact_root_for(iflow_relative)
        entry = artifact_roots.setdefault(
            root,
            {
                "package": _package_name(root, repo_name),
                "iflow": Path(iflow_relative).stem,
                "path": root,
                "artifact_root": (
                    Path(iflow_relative).parent.as_posix()
                    if root == "."
                    else Path(iflow_relative).relative_to(root).parent.as_posix()
                ),
                "changed_files": [],
                "has_iflow_change": False,
            },
        )
        if relative not in entry["changed_files"]:
            entry["changed_files"].append(relative)
        if Path(relative).suffix.lower() == IFLOW_SUFFIX:
            entry["has_iflow_change"] = True

    # 2) Sort so the output is deterministic across runs.
    matrix = sorted(artifact_roots.values(), key=lambda item: (item["path"], item["iflow"]))
    for entry in matrix:
        entry["changed_files"] = sorted(entry["changed_files"])

    return matrix


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Assembles the matrix of changed iFlows for the test generation workflow."
        )
    )
    parser.add_argument("--before", help="Previous SHA (event.before). Use empty for a full scan.")
    parser.add_argument("--after", default="HEAD", help="Current SHA (github.sha). Default: HEAD.")
    parser.add_argument("--repo", default=".", help="Repository path (default: .).")
    parser.add_argument("--repo-name", help="Repository name, used as the package name.")
    parser.add_argument(
        "--output",
        help="Output file. If omitted, writes the JSON to stdout.",
    )
    parser.add_argument(
        "--full-scan",
        action="store_true",
        help="Ignores the diff and considers every tracked iFlow.",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Prints diagnostics to stderr (does not pollute the matrix on stdout).",
    )
    return parser.parse_args(argv)


def _log(message: str, debug: bool) -> None:
    if debug:
        print(f"[build_matrix] {message}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    repo = Path(args.repo).resolve()
    if not repo.is_dir():
        print(f"[ERROR] repository not found: {repo}", file=sys.stderr)
        return 2

    repo_name = args.repo_name or repo.name

    try:
        changed: list[str] = []
        # Full scan when: explicitly requested, first push of a branch
        # (before zeroed) or `before` unavailable (force-push / shallow clone).
        if args.full_scan or not args.before or args.before == NULL_SHA:
            reason = "explicit" if args.full_scan else (
                "before missing" if not args.before else "before zeroed (new branch)"
            )
            _log(f"full scan: {reason}", args.debug)
            changed = tracked_iflow_files(repo)
        else:
            try:
                changed = changed_files_git(args.before, args.after, repo)
                _log(
                    f"diff {args.before[:7]}..{args.after[:7]} -> {len(changed)} file(s)",
                    args.debug,
                )
            except MatrixError as exc:
                # Force-push, shallow clone or unknown SHA: cannot diff.
                _log(f"diff unavailable ({exc}); falling back to full scan", args.debug)
                changed = tracked_iflow_files(repo)

        matrix = build_matrix(changed, repo, repo_name, force_full_scan=args.full_scan)
        _log(f"{len(matrix)} artifact(s) in the matrix", args.debug)

    except MatrixError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 - an unexpected failure must not become an "empty matrix"
        # Without this, an internal bug (e.g. AttributeError) would be silenced and
        # the workflow would conclude "nothing changed", failing to generate tests without warning.
        import traceback

        print(f"[ERROR] unexpected failure while assembling the matrix: {exc!r}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        return 2

    payload = json.dumps(matrix, ensure_ascii=False, separators=(",", ":"))

    if args.output:
        Path(args.output).write_text(payload, encoding="utf-8")
    else:
        print(payload)

    return 0


if __name__ == "__main__":
    sys.exit(main())
