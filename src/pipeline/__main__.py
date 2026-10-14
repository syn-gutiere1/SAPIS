"""
Entry point for `python -m pipeline`.

Delegates to the CLI, allowing:

    python -m pipeline --source <directory> --out <destination>
"""

from __future__ import annotations

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
