#!/usr/bin/env python3
"""Provide compatibility wrappers for older environment-based scripts.

New pipeline code should prefer CONFIG helpers, but some legacy paths still need
simple environment parsing. This module keeps those wrappers in one place so the
rest of the controller can continue moving toward explicit CONFIG-driven state."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .config import (
    is_true,
    load_default_config,
    load_variables_file_if_exists as load_config_file_if_exists,
    parse_bool_value,
    python_value_to_env,
)
from .logging import die


def export_config(config: dict[str, Any]) -> None:
    """Export CONFIG into os.environ for legacy code only."""
    for name, value in config.items():
        os.environ[name] = python_value_to_env(value)


def load_variables_file_if_exists(path: str | Path, *, overwrite: bool = False) -> bool:
    """Legacy helper: load variables.py into os.environ if present.

    Prefer lib.config.load_variables_file_if_exists(), which returns a dict.
    """
    config = load_config_file_if_exists(path)
    if not config:
        return False

    for name, value in config.items():
        if overwrite:
            os.environ[name] = python_value_to_env(value)
        else:
            os.environ.setdefault(name, python_value_to_env(value))

    return True


def load_default_variables(
    *,
    project_root_default: str | Path = "/workspace",
    cluster_target_default: str = "opentelemetry",
    export_project_root: bool = False,
    export_target_root: bool | None = None,
) -> None:
    """Legacy helper: load defaults and variables.py into os.environ."""
    config = load_default_config(
        project_root_default=project_root_default,
        cluster_target_default=cluster_target_default,
    )

    if not export_project_root:
        config.pop("PROJECT_ROOT", None)

    should_export_target_root = export_project_root if export_target_root is None else export_target_root
    if not should_export_target_root:
        config.pop("TARGET_ROOT", None)

    export_config(config)


def require_env(
    name: str,
    *,
    message: str | None = None,
    allow_empty: bool = False,
) -> str:
    """Require an environment variable to be set."""
    value = os.environ.get(name)

    if value is None or (value == "" and not allow_empty):
        die(message or f"{name} must be set")

    return "" if value is None else value


def normalize_bool_env(name: str) -> str:
    """Normalize a boolean environment variable to 'true' or 'false'."""
    value = parse_bool_value(require_env(name), name=name)
    os.environ[name] = "true" if value else "false"
    return os.environ[name]


def bool_env_value(var_name: str, default_value: str) -> str:
    """Get a boolean environment variable with a default, normalized as text."""
    value = parse_bool_value(os.environ.get(var_name, default_value), name=var_name)
    return "true" if value else "false"
