from __future__ import annotations

import json
import tomllib
from pathlib import Path

import pytest


BASE_LAB = """
[lab]
CLUSTER_TARGET = "5Gcore"
LAB_NAME = "honeypotlab"
FOLLOW_CONTROLLER_LOGS = false
SKIP_RESOURCE_CHECK = true
BUILD_CONTROLLER = true
BUILD_CONTAINERS = true
PROXY_BIND_ALL = false
EXPOSE_TO_HOST = true
HOST_SOCKET = false
CONTROLLER_PROXY_CONTAINER_PORT = 18080
REQUIRED_AVAIL_MEM_MB = 1024
REQUIRED_CPUS = 2
STEP_RETRY_ATTEMPTS_00 = 0
STEP_RETRY_DELAY_SECONDS_00 = 0
STEP_RETRY_ATTEMPTS_01 = 2
STEP_RETRY_DELAY_SECONDS_01 = 10
STEP_RETRY_ATTEMPTS_02 = 1
STEP_RETRY_DELAY_SECONDS_02 = 15
STEP_RETRY_ATTEMPTS_03 = 2
STEP_RETRY_DELAY_SECONDS_03 = 10
STEP_RETRY_ATTEMPTS_04 = 3
STEP_RETRY_DELAY_SECONDS_04 = 20
IMAGE_BUILD_PARALLELISM = 4
IMAGE_PUSH_PARALLELISM = 4
REGISTRY_PORT = 4999

[targets."5Gcore"]
TARGET_ONLY = "fiveg"
REGISTRY_PORT = 5000

[targets.opentelemetry]
TARGET_ONLY = "otel"
REGISTRY_PORT = 5001
"""


def write_config(tmp_path: Path, text: str = BASE_LAB) -> Path:
    path = tmp_path / "configuration.conf"
    path.write_text(text, encoding="utf-8")
    return path


@pytest.mark.unit
def test_load_project_config_merges_selected_target_and_ignores_env_override(
    host_importer,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_state = host_importer.module("lib.config_state")
    monkeypatch.setenv("CLUSTER_TARGET", "opentelemetry")
    monkeypatch.setenv("LAB_NAME", "from-env")

    config = config_state.load_project_config(write_config(tmp_path))

    assert config["CLUSTER_TARGET"] == "5Gcore"
    assert config["LAB_NAME"] == "honeypotlab"
    assert config["TARGET_ONLY"] == "fiveg"
    assert config["REGISTRY_PORT"] == 5000
    assert config["TARGET_CONFIG_SOURCE"] == "targets.5Gcore"


@pytest.mark.unit
def test_validate_config_rejects_missing_keys_and_bool_as_int(host_importer) -> None:
    config_state = host_importer.module("lib.config_state")
    valid = {name: False if expected is bool else 1 if expected is int else "x" for name, expected in config_state.LAB_SCHEMA.items()}
    config_state.validate_config(valid)

    missing = dict(valid)
    missing.pop("LAB_NAME")
    with pytest.raises(config_state.ConfigError, match="LAB_NAME"):
        config_state.validate_config(missing)

    invalid = dict(valid)
    invalid["REQUIRED_CPUS"] = True
    with pytest.raises(config_state.ConfigError, match="REQUIRED_CPUS must be int"):
        config_state.validate_config(invalid)


@pytest.mark.unit
def test_atomic_toml_and_json_round_trip(host_importer, tmp_path: Path) -> None:
    config_state = host_importer.module("lib.config_state")
    toml_path = tmp_path / "runtime" / "config.toml"
    json_path = tmp_path / "runtime" / "state.json"

    config_state.atomic_write_toml(toml_path, {"config": {"A": "x", "B": True, "N": 2}})
    config_state.atomic_write_json(json_path, {"ok": True, "items": [1, 2]})

    assert tomllib.loads(toml_path.read_text(encoding="utf-8"))["config"] == {"A": "x", "B": True, "N": 2}
    assert json.loads(json_path.read_text(encoding="utf-8")) == {"ok": True, "items": [1, 2]}


@pytest.mark.unit
def test_load_state_creates_default_and_rejects_non_object(host_importer, tmp_path: Path) -> None:
    config_state = host_importer.module("lib.config_state")
    missing = tmp_path / "missing.json"
    state = config_state.load_state(missing)

    assert state["schema_version"] == 1
    assert state["status"] == "created"
    assert state["values"] == {}

    invalid = tmp_path / "invalid.json"
    invalid.write_text("[]", encoding="utf-8")
    with pytest.raises(config_state.ConfigError, match="JSON object"):
        config_state.load_state(invalid)
