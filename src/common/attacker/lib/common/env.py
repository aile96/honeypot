#!/usr/bin/env python3
"""Shared environment helpers for attacker scripts."""

from __future__ import annotations

import os
from pathlib import Path


def env(name: str, default: str = "") -> str:
    """Return an environment variable with a string default."""
    return os.environ.get(name, default)


def data_path(*parts: str) -> Path:
    """Return a path below DATA_PATH."""
    return Path(env("DATA_PATH", "/tmp/KCData"), *parts)


def require_file(path: str | Path) -> Path:
    """Return an existing file path or raise FileNotFoundError."""
    candidate = Path(path)
    if not candidate.is_file():
        raise FileNotFoundError(str(candidate))
    return candidate
