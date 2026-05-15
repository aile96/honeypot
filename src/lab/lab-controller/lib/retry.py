#!/usr/bin/env python3
"""Provide generic retry logic for transient operations.

Pipeline operations often depend on Docker, Kind, or Kubernetes becoming ready.
This helper retries a caller-provided operation with logging and a fixed delay so
callers do not need to repeat retry loops."""

import time
from typing import Any, Callable

from .logging import warn
from .command import CommandError


def retry_operation(
    attempts: int,
    delay_seconds: int,
    label: str,
    operation: Callable[[], Any],
) -> bool:
    """Retry an operation with a delay between attempts."""
    last_error: Exception | None = None

    for attempt in range(1, attempts + 1):
        try:
            operation()
            return True

        except Exception as exc:
            last_error = exc

            if attempt < attempts:
                warn(
                    f"{label} failed on attempt {attempt}/{attempts}: {exc}. "
                    f"Retrying in {delay_seconds}s."
                )
                time.sleep(delay_seconds)

    warn(f"{label} failed after {attempts} attempt(s): {last_error}")
    return False


def retry_operation_or_raise(
    attempts: int,
    delay_seconds: int,
    label: str,
    operation: Callable[[], Any],
) -> None:
    """Retry an operation and raise CommandError on final failure."""
    last_error: Exception | None = None

    for attempt in range(1, attempts + 1):
        try:
            operation()
            return

        except Exception as exc:
            last_error = exc

            if attempt < attempts:
                warn(
                    f"{label} failed on attempt {attempt}/{attempts}: {exc}. "
                    f"Retrying in {delay_seconds}s."
                )
                time.sleep(delay_seconds)

    raise CommandError(f"{label} failed after {attempts} attempt(s): {last_error}")