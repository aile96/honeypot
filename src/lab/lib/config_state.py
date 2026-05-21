"""Typed TOML CONFIG and JSON STATE helpers for host bootstrap scripts."""

from __future__ import annotations

import json
import os
import tempfile
import tomllib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


class ConfigError(RuntimeError):
    """Raised when configuration is missing or invalid."""


LAB_SCHEMA: dict[str, type | tuple[type, ...]] = {
    "CLUSTER_TARGET": str,
    "LAB_NAME": str,
    "FOLLOW_CONTROLLER_LOGS": bool,
    "SKIP_RESOURCE_CHECK": bool,
    "BUILD_CONTROLLER": bool,
    "BUILD_CONTAINERS": bool,
    "PROXY_BIND_ALL": bool,
    "EXPOSE_TO_HOST": bool,
    "HOST_SOCKET": bool,
    "REQUIRED_AVAIL_MEM_MB": int,
    "REQUIRED_CPUS": int,
    "STEP_RETRY_ATTEMPTS_00": int,
    "STEP_RETRY_DELAY_SECONDS_00": int,
    "STEP_RETRY_ATTEMPTS_01": int,
    "STEP_RETRY_DELAY_SECONDS_01": int,
    "STEP_RETRY_ATTEMPTS_02": int,
    "STEP_RETRY_DELAY_SECONDS_02": int,
    "STEP_RETRY_ATTEMPTS_03": int,
    "STEP_RETRY_DELAY_SECONDS_03": int,
    "STEP_RETRY_ATTEMPTS_04": int,
    "STEP_RETRY_DELAY_SECONDS_04": int,
    "IMAGE_BUILD_PARALLELISM": int,
    "IMAGE_PUSH_PARALLELISM": int,
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _load_toml(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
    except FileNotFoundError as exc:
        raise ConfigError(f"Configuration file not found: {path}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"Invalid TOML in {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"Configuration file must contain a TOML table: {path}")
    return data


def _toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return str(value)
    if isinstance(value, list | tuple):
        return "[" + ", ".join(_toml_value(item) for item in value) + "]"
    text = "" if value is None else str(value)
    return json.dumps(text)


def _write_table(lines: list[str], name: str, values: Mapping[str, Any]) -> None:
    lines.append(f"[{name}]")
    for key in sorted(values):
        value = values[key]
        if isinstance(value, dict):
            continue
        lines.append(f"{key} = {_toml_value(value)}")
    lines.append("")


def atomic_write_toml(path: str | Path, tables: Mapping[str, Mapping[str, Any]]) -> None:
    """Atomically write simple TOML tables."""
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    for name, values in tables.items():
        _write_table(lines, name, values)
    atomic_write_text(out, "\n".join(lines).rstrip() + "\n")


def atomic_write_json(path: str | Path, payload: Mapping[str, Any]) -> None:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(out, json.dumps(payload, indent=2, sort_keys=True) + "\n")


def atomic_write_text(path: Path, text: str) -> None:
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent), text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(tmp_name, path)
    finally:
        Path(tmp_name).unlink(missing_ok=True)


def validate_config(config: Mapping[str, Any], schema: Mapping[str, type | tuple[type, ...]] = LAB_SCHEMA) -> None:
    """Validate required config keys and their Python types."""
    missing = [key for key in schema if key not in config]
    if missing:
        raise ConfigError("Missing required configuration value(s): " + ", ".join(missing))

    for key, expected in schema.items():
        value = config[key]
        if expected is int and isinstance(value, bool):
            raise ConfigError(f"{key} must be int, got bool")
        if not isinstance(value, expected):
            expected_name = getattr(expected, "__name__", str(expected))
            raise ConfigError(f"{key} has invalid type: expected {expected_name}, got {type(value).__name__}")


def _selected_target(data: Mapping[str, Any], target: str) -> dict[str, Any]:
    targets = data.get("targets", {})
    if not isinstance(targets, dict):
        raise ConfigError("[targets] must be a TOML table")
    selected = targets.get(target)
    if not isinstance(selected, dict):
        raise ConfigError(f"Missing [targets.{target!r}] section in configuration")
    return dict(selected)


def load_project_config(path: str | Path) -> dict[str, Any]:
    """Load root configuration.conf and flatten lab plus selected target values."""
    config_path = Path(path)
    data = _load_toml(config_path)
    lab = data.get("lab")
    if not isinstance(lab, dict):
        raise ConfigError("Missing [lab] section in configuration.conf")
    validate_config(lab)
    target = str(lab["CLUSTER_TARGET"])
    merged: dict[str, Any] = {}
    merged.update(lab)
    merged.update(_selected_target(data, target))
    merged["CONFIG_SOURCE_FILE"] = str(config_path)
    merged["TARGET_CONFIG_SOURCE"] = f"targets.{target}"
    return merged


def load_runtime_config(path: str | Path) -> dict[str, Any]:
    """Load a persisted runtime config written by start.py."""
    data = _load_toml(Path(path))
    config = data.get("config", data)
    if not isinstance(config, dict):
        raise ConfigError(f"Runtime config must contain a [config] table: {path}")
    return {str(key): value for key, value in config.items() if not isinstance(value, dict)}


def load_state(path: str | Path) -> dict[str, Any]:
    state_path = Path(path)
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {
            "schema_version": 1,
            "status": "created",
            "created_at": utc_now(),
            "updated_at": utc_now(),
            "values": {},
        }
    except json.JSONDecodeError as exc:
        raise ConfigError(f"Invalid JSON state file {state_path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"State file must contain a JSON object: {state_path}")
    data.setdefault("values", {})
    return data


def save_state(path: str | Path, state: Mapping[str, Any]) -> None:
    payload = dict(state)
    payload["updated_at"] = utc_now()
    atomic_write_json(path, payload)
