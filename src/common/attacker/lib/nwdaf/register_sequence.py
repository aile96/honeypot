#!/usr/bin/env python3
"""Register the test NWDAF container as SMF, AMF, UDM, and PCF in sequence."""

from __future__ import annotations

import os
import sys
import time

try:
    from .nrf_register import env_int, register_profile
except ImportError:  # pragma: no cover - supports direct execution by path.
    from nwdaf.nrf_register import env_int, register_profile


def sequence() -> list[str]:
    """Return the NF types to register in order."""
    raw = os.getenv("SEQUENCE_NF_TYPES", "SMF,AMF,UDM,PCF")
    return [item.strip().upper() for item in raw.split(",") if item.strip()]


def main() -> int:
    """Register one profile per NF type and fail on the first NRF rejection."""
    delay = env_int("REGISTER_SEQUENCE_DELAY_SECONDS", 5)

    for nf_type in sequence():
        status, body = register_profile(nf_type)
        print(f"registered-as={nf_type} status={status} body={body}", flush=True)
        if not 200 <= status < 300:
            return 1
        time.sleep(delay)

    return 0


if __name__ == "__main__":
    sys.exit(main())
