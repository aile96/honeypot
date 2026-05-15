#!/usr/bin/env python3
"""Load, normalize, and validate CONFIG values for the lab controller.

CONFIG is an explicit dictionary loaded from variables.py plus approved runtime
overrides. These helpers parse typed values, preserve values for subprocess-local
environments, enforce required keys, and avoid accidentally importing unrelated
process environment variables into the pipeline."""

from __future__ import annotations

import os
import re
import runpy
from pathlib import Path
from typing import Any, Mapping, MutableMapping, TypeAlias

from .logging import die

Config: TypeAlias = dict[str, Any]

_CONFIG_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_TRUE_VALUES = {"1", "true", "yes", "y", "on"}
_FALSE_VALUES = {"0", "false", "no", "n", "off", ""}


def python_value_to_env(value: Any) -> str:
    """Convert a Python value to a string suitable for subprocess env vars."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return ""
    return str(value)


def validate_config_name(name: str, *, source: str | Path | None = None) -> None:
    """Validate a variables.py-style config name."""
    if _CONFIG_NAME_RE.match(name):
        return
    location = f" in {source}" if source is not None else ""
    die(f"Invalid config variable name '{name}'{location}")


def load_variables_file(path: str | Path) -> Config:
    """Load a variables.py file into an in-memory CONFIG dictionary.

    Expected format:

        variables = [
            {"name": "PROJECT_ROOT", "value": "/workdir"},
            {"name": "STEP_RETRY_ATTEMPTS_03_BUILD_AND_DEPLOY_SKAFFOLD_STACK", "value": 1},
        ]
    """
    variables_path = Path(path)

    if not variables_path.is_file():
        die(f"Variables file not found: {variables_path}")

    data = runpy.run_path(str(variables_path))
    variables = data.get("variables")

    if variables is None:
        die(f"Missing 'variables' list in: {variables_path}")

    if not isinstance(variables, list):
        die(f"'variables' must be a list in: {variables_path}")

    config: Config = {}

    for item in variables:
        if not isinstance(item, dict):
            die(f"Invalid variable entry in {variables_path}: {item!r}")

        name = item.get("name")
        value = item.get("value")

        if not isinstance(name, str):
            die(f"Invalid variable name in {variables_path}: {item!r}")

        validate_config_name(name, source=variables_path)
        config[name] = value

    return config


def load_variables_file_if_exists(path: str | Path) -> Config:
    """Load variables.py when present; otherwise return an empty CONFIG."""
    variables_path = Path(path)
    if not variables_path.is_file():
        return {}
    return load_variables_file(variables_path)


def merge_config(*configs: Mapping[str, Any], overwrite: bool = True) -> Config:
    """Merge CONFIG dictionaries.

    With overwrite=True, later configs win. With overwrite=False, first value wins.
    """
    merged: Config = {}
    for config in configs:
        for name, value in config.items():
            validate_config_name(name)
            if overwrite or name not in merged:
                merged[name] = value
    return merged


def set_config_default(config: MutableMapping[str, Any], name: str, value: Any) -> Any:
    """Set a default value in CONFIG and return the stored value."""
    validate_config_name(name)
    if name not in config or config[name] is None:
        config[name] = value
    return config[name]


def require_config(
    config: Mapping[str, Any],
    name: str,
    *,
    message: str | None = None,
    allow_empty: bool = False,
) -> Any:
    """Require a CONFIG key to exist and optionally be non-empty."""
    if name not in config:
        die(message or f"{name} must be set")

    value = config[name]

    if not allow_empty and (value is None or value == ""):
        die(message or f"{name} must be set")

    return value


def config_str(
    config: Mapping[str, Any],
    name: str,
    default: Any = None,
    *,
    allow_empty: bool = True,
) -> str:
    """Read a CONFIG value as a string."""
    if default is None:
        value = require_config(config, name, allow_empty=allow_empty)
    else:
        value = config.get(name, default)
        if not allow_empty and (value is None or value == ""):
            die(f"{name} must be set")
    return python_value_to_env(value)


def config_path(
    config: Mapping[str, Any],
    name: str,
    default: Any = None,
    *,
    allow_empty: bool = False,
) -> Path:
    """Read a CONFIG value as pathlib.Path."""
    value = config_str(config, name, default, allow_empty=allow_empty)
    if not value and allow_empty:
        return Path("")
    return Path(value)


def config_int(
    config: Mapping[str, Any],
    name: str,
    default: Any = None,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    """Read a CONFIG value as an integer with optional bounds."""
    raw = config_str(config, name, default, allow_empty=False)

    if not re.match(r"^[0-9]+$", raw):
        bound = f" >= {minimum}" if minimum is not None else ""
        die(f"{name} must be an integer{bound} (got '{raw}').")

    value = int(raw)

    if minimum is not None and value < minimum:
        die(f"{name} must be >= {minimum} (got '{raw}').")

    if maximum is not None and value > maximum:
        die(f"{name} must be <= {maximum} (got '{raw}').")

    return value


def config_float(
    config: Mapping[str, Any],
    name: str,
    default: Any = None,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float:
    """Read a CONFIG value as a float with optional bounds."""
    raw = config_str(config, name, default, allow_empty=False)

    try:
        value = float(raw)
    except ValueError:
        die(f"{name} must be a number (got '{raw}').")

    if minimum is not None and value < minimum:
        die(f"{name} must be >= {minimum} (got '{raw}').")

    if maximum is not None and value > maximum:
        die(f"{name} must be <= {maximum} (got '{raw}').")

    return value


def parse_bool_value(value: Any, *, name: str = "value") -> bool:
    """Parse a shell-style boolean value.

    Raises:
        ValueError: if value is empty or not a valid boolean.
    """
    if value is None or str(value).strip() == "":
        raise ValueError(f"{name} must be a boolean value")

    if isinstance(value, bool):
        return value

    normalized = str(value).strip().lower()

    if normalized in _TRUE_VALUES:
        return True

    if normalized in _FALSE_VALUES:
        return False

    raise ValueError(f"{name} must be a boolean value, got: {value!r}")


def parse_positive_float_value(value: Any, *, name: str = "value") -> float:
    """Parse a positive float.

    Raises:
        ValueError: if value is empty, not numeric, or <= 0.
    """
    if value is None or str(value).strip() == "":
        raise ValueError(f"{name} must be a positive number")

    raw = str(value).strip()

    try:
        parsed = float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a positive number, got: {raw!r}") from exc

    if parsed <= 0:
        raise ValueError(f"{name} must be greater than zero, got: {raw!r}")

    return parsed


def is_true(value: Any) -> bool:
    """Return True when value represents a true boolean value."""
    return parse_bool_value(value)


def config_bool(config: Mapping[str, Any], name: str, default: Any = False) -> bool:
    """Read a CONFIG value as boolean."""
    return parse_bool_value(config.get(name, default), name=name)


def normalize_bool_config(
    config: MutableMapping[str, Any],
    name: str,
    *,
    default: Any = None,
) -> str:
    """Normalize a boolean CONFIG value in-place to 'true' or 'false'."""
    if name not in config:
        if default is None:
            die(f"{name} must be set")
        config[name] = default

    normalized = "true" if config_bool(config, name) else "false"
    config[name] = normalized
    return normalized


def bool_config_value(config: Mapping[str, Any], name: str, default_value: Any) -> str:
    """Return a boolean CONFIG value as 'true' or 'false'."""
    return "true" if config_bool(config, name, default_value) else "false"


def require_port_config(config: Mapping[str, Any], name: str) -> int:
    """Require a valid TCP port in CONFIG."""
    return config_int(config, name, minimum=1, maximum=65535)


def config_to_env(
    config: Mapping[str, Any] | None,
    *,
    base_env: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Convert CONFIG to an environment dictionary for a subprocess only.

    This does not mutate os.environ. By default, the base is os.environ so PATH,
    HOME, DOCKER_HOST, KUBECONFIG, proxy variables, etc. stay available.
    """
    env = dict(os.environ if base_env is None else base_env)

    if config is None:
        return env

    for name, value in config.items():
        validate_config_name(name)
        env[name] = python_value_to_env(value)

    return env


def load_default_config(
    *,
    project_root_default: str | Path = "/workspace",
    cluster_target_default: str = "opentelemetry",
    env_file: str | Path | None = None,
    target_config_file: str | Path | None = None,
    overlay_env: bool = False,
) -> Config:
    """Load the standard project CONFIG without mutating os.environ."""
    project_root = Path(os.environ.get("PROJECT_ROOT", str(project_root_default))).resolve()
    cluster_target = os.environ.get("CLUSTER_TARGET", cluster_target_default)
    target_root = project_root / "src" / cluster_target

    default_config: Config = {
        "PROJECT_ROOT": str(project_root),
        "CLUSTER_TARGET": cluster_target,
        "TARGET_ROOT": str(target_root),
    }

    main_env_file = Path(env_file or os.environ.get("ENV_FILE", project_root / "variables.py"))
    target_env_file = Path(
        target_config_file
        or os.environ.get("TARGET_CONFIG_FILE", target_root / "conf-files" / "variables.py")
    )

    config = merge_config(
        default_config,
        load_variables_file_if_exists(main_env_file),
        load_variables_file_if_exists(target_env_file),
        overwrite=True,
    )

    if overlay_env:
        for name in list(config):
            if name in os.environ:
                config[name] = os.environ[name]

    return config
