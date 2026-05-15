#!/usr/bin/env python3
"""Wait for HTTP services to become reachable.

Hooks use this helper when a service exposes a simple health or readiness URL. It
polls until a non-fatal HTTP status is returned or the timeout expires, while
recording the last observed error for useful diagnostics."""

from __future__ import annotations

import time
import urllib.request
from .logging import warn


def wait_http(url: str, *, timeout_seconds: int = 120, status_lt: int = 500) -> bool:
    """Wait for an HTTP endpoint to return a non-fatal status."""
    deadline = time.monotonic() + timeout_seconds
    last_error = ""

    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=3) as response:
                if response.status < status_lt:
                    return True
        except Exception as exc:
            last_error = repr(exc)
        time.sleep(2)

    warn(f"HTTP endpoint did not become ready: {url}; last_error={last_error}")
    return False
