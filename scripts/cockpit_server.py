"""P27-nonies - stable entry point for the local APD cockpit server.

The historical launcher still points to ``scripts/apd_pipeline_web.py``; this
module keeps the documented ``cockpit_server`` name working without changing
the HTTP contract.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.apd_pipeline_web import build_parser, main  # noqa: E402

__all__ = ["build_parser", "main"]


if __name__ == "__main__":
    raise SystemExit(main())
