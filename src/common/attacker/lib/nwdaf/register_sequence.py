#!/usr/bin/env python3
"""Register the test NWDAF container as SMF, AMF, UDM, and PCF in sequence."""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request

try:
    from .nrf_register import (
        authorization_headers,
        build_profile,
        env_int,
        nrf_base_url,
        register_profile,
        request_context,
    )
except ImportError:  # pragma: no cover - supports direct execution by path.
    from nwdaf.nrf_register import (
        authorization_headers,
        build_profile,
        env_int,
        nrf_base_url,
        register_profile,
        request_context,
    )


def sequence() -> list[str]:
    """Return the NF types to register in order."""
    raw = os.getenv("SEQUENCE_NF_TYPES", "SMF,AMF,UDM,PCF")
    return [item.strip().upper() for item in raw.split(",") if item.strip()]


def verify_profile_registered(nf_type: str) -> bool:
    """Read back the deterministic NF profile and verify NRF persisted it."""
    expected = build_profile(nf_type)
    base_url = nrf_base_url()
    url = f"{base_url.rstrip('/')}/nnrf-nfm/v1/nf-instances/{expected['nfInstanceId']}"
    headers = {"Accept": "application/json"}
    headers.update(authorization_headers(nf_type, instance_id=expected["nfInstanceId"], base_url=base_url))
    request = urllib.request.Request(url, method="GET", headers=headers)
    try:
        with urllib.request.urlopen(
            request,
            context=request_context(base_url),
            timeout=env_int("NRF_TIMEOUT_SECONDS", 10),
        ) as response:
            body = response.read().decode("utf-8", errors="replace")
            payload = json.loads(body) if body else {}
    except (urllib.error.HTTPError, urllib.error.URLError, json.JSONDecodeError) as exc:
        print(f"verification-failed nfType={nf_type} error={exc}", flush=True)
        return False

    ok = (
        isinstance(payload, dict)
        and payload.get("nfInstanceId") == expected["nfInstanceId"]
        and str(payload.get("nfType", "")).upper() == nf_type.upper()
        and str(payload.get("nfStatus", "")).upper() == "REGISTERED"
    )
    if not ok:
        print(f"verification-failed nfType={nf_type} persisted={payload}", flush=True)
    return ok


def main() -> int:
    """Register one profile per NF type and fail on the first NRF rejection."""
    delay = env_int("REGISTER_SEQUENCE_DELAY_SECONDS", 5)

    for nf_type in sequence():
        status, body = register_profile(nf_type)
        print(f"registered-as={nf_type} status={status} body={body}", flush=True)
        if not 200 <= status < 300:
            return 1
        if not verify_profile_registered(nf_type):
            return 1
        time.sleep(delay)

    return 0


if __name__ == "__main__":
    sys.exit(main())
