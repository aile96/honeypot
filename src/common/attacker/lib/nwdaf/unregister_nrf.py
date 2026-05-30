#!/usr/bin/env python3
"""Deregister the test NWDAF profiles from every configured NRF."""

from __future__ import annotations

import os
import sys

try:
    from .nrf_register import env_bool, nrf_base_urls, unregister_profile
except ImportError:  # pragma: no cover - supports direct execution by path.
    from nwdaf.nrf_register import env_bool, nrf_base_urls, unregister_profile


def nf_types() -> list[str]:
    """Return the NF types that should be removed from NRF."""
    raw = os.getenv("UNREGISTER_NF_TYPES", os.getenv("SEQUENCE_NF_TYPES", "NWDAF,SMF,AMF,UDM,PCF"))
    return [item.strip().upper() for item in raw.split(",") if item.strip()]


def main() -> int:
    """Remove deterministic test NF registrations from all configured NRFs."""
    ignore_not_found = env_bool("UNREGISTER_IGNORE_NOT_FOUND", True)
    ignore_forbidden = env_bool("UNREGISTER_IGNORE_FORBIDDEN", False)
    failed = False

    for base_url in nrf_base_urls():
        for nf_type in nf_types():
            status, body = unregister_profile(nf_type, base_url=base_url)
            print(f"deregistered-from={base_url} nfType={nf_type} status={status} body={body}", flush=True)
            if 200 <= status < 300:
                continue
            if status == 404 and ignore_not_found:
                continue
            if status in {401, 403} and ignore_forbidden:
                print(f"unregister-blocked-by-auth nfType={nf_type} status={status}", flush=True)
                continue
            failed = True

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
