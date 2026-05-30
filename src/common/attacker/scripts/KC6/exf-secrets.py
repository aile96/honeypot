#!/usr/bin/env python3
"""Thin compatibility wrapper for KC6 node secret collection.

The implementation lives in ./lib/exf_secrets_impl.py so the CALDERA ability can
stay stable while the script logic remains split into smaller units.
"""

from __future__ import annotations

import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR / "lib"))

from exf_secrets_impl import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
