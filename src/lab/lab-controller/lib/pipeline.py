#!/usr/bin/env python3
"""Provide reusable pipeline discovery and retry helpers.

The runner uses this module to find ordered step scripts, resolve matching hooks,
and interpret retry policy values. Keeping these helpers separate makes the main
start_lab.py flow easier to read and keeps hook lookup rules consistent."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Iterable, Mapping

from .config import config_int
from .logging import die

DEFAULT_STEP_RETRY_ATTEMPTS = 0
DEFAULT_STEP_RETRY_DELAY_SECONDS = 0


def is_pipeline_script(path: str | Path) -> bool:
    """Return True when path is an executable pipeline step file.

    The lab runner executes Python files directly inside PIPELINE_ROOT, sorted by
    filename. Private/helper files starting with '_' and package files such as
    __init__.py are ignored.
    """
    item = Path(path)
    if not item.is_file():
        return False
    if item.suffix != ".py":
        return False
    if item.name == "__init__.py":
        return False
    if item.name.startswith("_"):
        return False
    return True


def discover_pipeline_scripts(pipeline_root: str | Path) -> list[Path]:
    """Discover all Python step scripts directly inside PIPELINE_ROOT."""
    root = Path(pipeline_root)
    if not root.is_dir():
        die(f"Pipeline root not found or not a directory: {root}")

    return sorted(
        (item for item in root.iterdir() if is_pipeline_script(item)),
        key=lambda item: item.name,
    )


def step_index_from_script(step_script: str | Path) -> str | None:
    """Extract the numeric step index from a filename like 20_create_cluster.py."""
    match = re.match(r"^([0-9]{2})_", Path(step_script).name)
    return match.group(1) if match else None


def step_key_from_script(step_script: str | Path) -> str:
    """Build the suffix used by step-specific config keys."""
    step_name = Path(step_script).stem.upper()
    return re.sub(r"[^A-Z0-9]", "_", step_name)


def step_slug_from_script(step_script: str | Path) -> str:
    """Return a stable slug without the numeric prefix."""
    stem = Path(step_script).stem
    return re.sub(r"^[0-9]{2}_", "", stem)


def parse_pipeline_steps(value: Any, default_steps: Iterable[str]) -> list[str]:
    """Parse PIPELINE_STEPS as a list or comma-separated string.

    Kept for compatibility, but the preferred start_lab.py behavior is automatic
    discovery via discover_pipeline_scripts().
    """
    if value is None:
        return list(default_steps)

    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]

    if isinstance(value, list | tuple):
        return [str(item) for item in value]

    die("PIPELINE_STEPS must be a list or comma-separated string.")


def resolve_step_retry_policy(
    step_script: str | Path,
    config: Mapping[str, Any],
) -> tuple[int, int]:
    """Resolve retry count and delay for one pipeline unit.

    Only unit-specific variables are honored. For a script whose key is FOO,
    STEP_RETRY_ATTEMPTS_FOO=0 means run once and do not retry after failure,
    STEP_RETRY_ATTEMPTS_FOO=1 means retry once after the first failure, and so on.
    """
    step_key = step_key_from_script(step_script)

    attempts_name = f"STEP_RETRY_ATTEMPTS_{step_key}"
    delay_name = f"STEP_RETRY_DELAY_SECONDS_{step_key}"

    retries = config_int(
        config,
        attempts_name,
        DEFAULT_STEP_RETRY_ATTEMPTS,
        minimum=0,
    )

    delay = config_int(
        config,
        delay_name,
        DEFAULT_STEP_RETRY_DELAY_SECONDS,
        minimum=0,
    )

    return retries, delay


def resolve_hook_candidates(
    hooks_dir: str | Path,
    kind: str,
    step_script: str | Path,
) -> list[Path]:
    """Return candidate hook paths for a step.

    Supported names:
        HOOK_PRE_01.py
        HOOK_PRE_01_render_and_create_kind_cluster.py
        HOOK_POST_01.py
        HOOK_POST_01_render_and_create_kind_cluster.py
    """
    step_index = step_index_from_script(step_script)
    if step_index is None:
        return []

    slug = step_slug_from_script(step_script)
    prefix = f"HOOK_{kind.upper()}_{step_index}"
    base = Path(hooks_dir)

    return [
        base / f"{prefix}.py",
        base / f"{prefix}_{slug}.py",
    ]
