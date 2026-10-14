#!/usr/bin/env python3
"""
run.py - pipeline launcher for use in CI and locally.

It exists because `python -m pipeline.cli` requires `pipeline` to already be
importable - that is, it requires PYTHONPATH to be exported. This launcher adjusts
`sys.path` before importing, so it always works:

    python run.py --source <folder> --out <destination>
    python run.py --mode extract --source <folder>
"""

from __future__ import annotations

import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
SRC = RAIZ / "src"

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from pipeline.cli import main  # noqa: E402 - must come after the sys.path adjustment

if __name__ == "__main__":
    sys.exit(main())
