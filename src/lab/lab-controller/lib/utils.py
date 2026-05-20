#!/usr/bin/env python3
"""Provide miscellaneous path and template utilities.

The helpers in this module resolve project-relative paths. Template variable
substitution is imported from the shared host/controller helper package.
"""

from __future__ import annotations

from pathlib import Path

from shared.templates import substitute_vars


def resolve_project_path(project_root: Path, value: str | Path) -> Path:
    """Resolve a path relative to project_root."""
    path = Path(value)
    if path.is_absolute():
        return path
    return project_root / str(value).removeprefix("./")
