#!/usr/bin/env python3
"""Deregister the test NWDAF service profiles from NRF."""

from __future__ import annotations

import sys
from pathlib import Path


def add_attacker_lib_path() -> None:
    """Prefer the local attacker library when the script runs outside the image."""
    for parent in Path(__file__).resolve().parents:
        lib_dir = parent / "lib"
        if (lib_dir / "nwdaf" / "unregister_nrf.py").is_file():
            sys.path.insert(0, str(lib_dir))
            return


def main() -> int:
    add_attacker_lib_path()
    from nwdaf.unregister_nrf import main as unregister_main

    return unregister_main()


if __name__ == "__main__":
    sys.exit(main())
