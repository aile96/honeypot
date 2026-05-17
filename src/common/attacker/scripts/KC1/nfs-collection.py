#!/usr/bin/env python3
"""Run the free5GC NF collection helper next to this entrypoint."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def main() -> int:
    helper = Path(__file__).with_name("nfs_collection.py")
    if not helper.is_file():
        print(f"Missing helper: {helper}", file=sys.stderr)
        return 1
    return subprocess.run([sys.executable or "python3", str(helper), *sys.argv[1:]], text=True).returncode


if __name__ == "__main__":
    raise SystemExit(main())
