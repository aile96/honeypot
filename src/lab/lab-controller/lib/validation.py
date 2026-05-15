#!/usr/bin/env python3
"""Provide validation helpers for CONFIG and environment values.

This module contains focused checks for integer ranges, booleans, ports, paths,
and safe names. Centralizing validation keeps error messages consistent and keeps
pipeline steps focused on orchestration rather than input parsing."""

from __future__ import annotations

import os
import re
from typing import Any, Mapping

from .config import require_port_config
from .logging import die


def require_non_negative_int(name: str, value: Any) -> int:
    """Require a non-negative integer."""
    raw = str(value)
    if not re.match(r"^[0-9]+$", raw):
        die(f"{name} must be a non-negative integer")
    return int(raw)


def require_int_at_least(name: str, value: Any, minimum: int) -> int:
    """Require an integer >= minimum."""
    raw = str(value)
    if not re.match(r"^[0-9]+$", raw):
        die(f"{name} must be an integer >= {minimum}")

    parsed = int(raw)
    if parsed < minimum:
        die(f"{name} must be >= {minimum}")
    return parsed


def require_port(name: str, value: Any) -> int:
    """Require a valid TCP port value."""
    port = require_int_at_least(name, value, 1)
    if port > 65535:
        die(f"{name} must be between 1 and 65535, got '{value}'.")
    return port


def require_port_from_config(config: Mapping[str, Any], name: str) -> int:
    """Require a valid TCP port from CONFIG."""
    return require_port_config(config, name)


def require_port_env(name: str) -> int:
    """Legacy helper: require a valid TCP port from os.environ."""
    value = os.environ.get(name)
    if value is None:
        die(f"{name} must be set")
    return require_port(name, value)
