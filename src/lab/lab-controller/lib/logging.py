#!/usr/bin/env python3
"""Provide small logging helpers shared by controller scripts.

All pipeline steps and hooks use these functions for timestamped informational,
warning, error, and fatal messages. Keeping logging centralized makes command
output easier to scan and ensures fatal errors exit in a consistent way."""

from __future__ import annotations

import sys
from datetime import datetime


def timestamp() -> str:
    """Return a local timestamp suitable for human logs."""
    return datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")


def log(*args: object) -> None:
    """Print an info message."""
    print("[INFO]", *args, flush=True)


def warn(*args: object) -> None:
    """Print a warning message to stderr."""
    print("[WARN]", *args, file=sys.stderr, flush=True)


def err(*args: object) -> None:
    """Print an error message to stderr."""
    print("[ERROR]", *args, file=sys.stderr, flush=True)


def die(*args: object, exit_code: int = 1) -> None:
    """Print an error message and exit."""
    err(*args)
    raise SystemExit(exit_code)
