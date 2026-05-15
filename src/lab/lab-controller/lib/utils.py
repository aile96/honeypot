#!/usr/bin/env python3
"""Provide miscellaneous path and template utilities.

The shared helpers in this module resolve project-relative paths and substitute
shell-style variables in target templates. They are intentionally small and free
of target-specific assumptions."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Mapping

from .config import config_str


def resolve_project_path(project_root: Path, value: str | Path) -> Path:
    """Resolve a path relative to project_root."""
    path = Path(value)
    if path.is_absolute():
        return path
    return project_root / str(value).removeprefix("./")


def resolve_config_path(
    config: Mapping[str, Any],
    name: str,
    *,
    root_name: str = "CODE_ROOT",
) -> Path:
    """Resolve CONFIG[name] relative to CONFIG[root_name]."""
    project_root = Path(config_str(config, root_name, allow_empty=False))
    return resolve_project_path(project_root, config_str(config, name, allow_empty=False))


def substitute_vars(text: str, values: Mapping[str, Any]) -> str:
    """Substitute ${VAR} and ${VAR:-default} placeholders."""

    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        default = match.group(2) or ""
        value = values.get(name, "")
        return default if value is None or str(value) == "" else str(value)

    return re.sub(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}", replace, text)
