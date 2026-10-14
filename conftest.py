"""
Test configuration for the hub.

Two responsibilities:

1. Ensure that `src/` is on `sys.path` before any test import, so that
   `pipeline.*` is importable even when pytest is invoked from outside the
   repository root.

2. Provide the `tmp_path` fixture.

   The native `tmp_path` depends on `tmp_path_factory`, which *garbage
   collects* the previous numbered directories (`pytest-of-<usuario>`).
   In environments with write restrictions - the development sandbox used
   to build this hub, or runners with a restrictive /tmp policy - that
   collection fails with PermissionError and takes down the setup of every
   test that uses the fixture. Here we create a single directory, with the
   same semantics, without depending on the collection.

   The semantics are identical to those of the native `tmp_path`: a new and
   empty `Path` per test. Each test receives its own directory, so there is no
   interference between cases.
"""

from __future__ import annotations

import itertools
import os
import sys
import tempfile
import uuid
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent / "src"

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


_counter = itertools.count()


def _novo_diretorio(prefixo: str) -> Path:
    """
    Create a unique directory under the system temporary root.

    It neither reuses nor deletes anything: removal is precisely the operation
    unavailable in the environments that motivated this module.
    """
    base = Path(tempfile.gettempdir()) / "sap-is-test-hub-tests"
    base.mkdir(parents=True, exist_ok=True)

    for _ in range(100):
        candidate = base / f"{prefixo}-{os.getpid()}-{next(_counter)}-{uuid.uuid4().hex[:8]}"
        try:
            candidate.mkdir()
        except FileExistsError:  # pragma: no cover - unlikely collision
            continue
        return candidate

    raise RuntimeError(f"Could not create a temporary directory in {base}")


@pytest.fixture
def tmp_path() -> Path:
    """
    Temporary directory exclusive to each test.

    Replaces the native fixture, which fails when the system does not allow
    removing temporary directories. The semantics are the same: a new and
    empty `Path` for each test.
    """
    return _novo_diretorio("t")


@pytest.fixture(scope="session")
def tmp_path_factory() -> Path:
    """
    Base directory exclusive to each session.

    Replaces the native `tmp_path_factory`, which collects the previous
    numbered directories and fails with PermissionError in restricted
    environments.
    """
    return _novo_diretorio("s")
