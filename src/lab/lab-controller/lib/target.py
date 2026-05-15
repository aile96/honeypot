#!/usr/bin/env python3
"""Resolve target paths and target-owned configuration values.

The lab controller mounts one target under CODE_ROOT. These helpers resolve paths
inside that target, locate conf-files and generated directories, and parse list
settings from CONFIG so steps and hooks do not hard-code target layout details."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable, Mapping

from .config import config_str


def target_root(config: Mapping[str, Any]) -> Path:
    """Return the mounted target source root."""
    return Path(config_str(config, "CODE_ROOT", "/workdir/code"))


def target_conf_dir(config: Mapping[str, Any]) -> Path:
    """Return the target conf-files directory."""
    return target_root(config) / "conf-files"


def target_conf_file(config: Mapping[str, Any], name: str) -> Path:
    """Return a file inside the target conf-files directory."""
    return target_conf_dir(config) / name


def generated_dir(config: Mapping[str, Any]) -> Path:
    """Return the writable generated artifact directory."""
    return Path(config_str(config, "GENERATED_DIR", "/res/runtime/generated"))


def runtime_dir(config: Mapping[str, Any], *parts: str) -> Path:
    """Return a writable path below RUNTIME_DIR."""
    return Path(config_str(config, "RUNTIME_DIR", "/res/runtime")).joinpath(*parts)


def config_list(
    config: Mapping[str, Any],
    name: str,
    default: Iterable[str] | None = None,
) -> list[str]:
    """Read a CONFIG value as a list of non-empty strings."""
    value = config.get(name)
    if value is None or value == "":
        return list(default or [])

    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]

    if isinstance(value, list | tuple | set):
        return [str(item).strip() for item in value if str(item).strip()]

    return [str(value).strip()] if str(value).strip() else list(default or [])
