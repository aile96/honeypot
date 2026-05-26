from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest


@pytest.fixture
def start_module(host_importer):
    return host_importer.script("honeypot_start", Path("start.py").resolve())


@pytest.mark.unit
def test_prepare_config_derives_lab_paths_and_names(start_module) -> None:
    config = start_module.prepare_config(
        {
            "LAB_NAME": "honeypotlab",
            "CLUSTER_TARGET": "5Gcore",
            "HOST_SOCKET": False,
            "EXPOSE_TO_HOST": True,
            "PROXY_BIND_ALL": False,
            "CONTROLLER_PROXY_CONTAINER_PORT": 18080,
        }
    )

    assert config["CONTROLLER_CONTAINER_NAME"] == "honeypotlab-controller"
    assert config["COMPOSE_PROJECT_NAME"] == "honeypot-honeypotlab"
    assert config["KUBE_CONTEXT"] == "kind-honeypotlab"
    assert config["HOST_CODE_ROOT"].endswith("src/5Gcore")
    assert config["CONFIG_FILE"] == "/runtime/config.toml"
    assert config["STATE_FILE"] == "/res/runtime/honeypotlab/generated/lab-state.json"
    assert config["DOCKER_DATA_ROOT"] == "/res/runtime/honeypotlab/docker-data"
    assert config["AUTOREMOVE_LAB"] is False
    assert "CACHE_DIR" not in config


@pytest.mark.unit
def test_write_info_records_proxy_exposure(start_module, tmp_path: Path) -> None:
    config = start_module.prepare_config(
        {
            "LAB_NAME": "honeypotlab",
            "CLUSTER_TARGET": "5Gcore",
            "HOST_SOCKET": False,
            "EXPOSE_TO_HOST": True,
            "PROXY_BIND_ALL": False,
            "CONTROLLER_PROXY_CONTAINER_PORT": 18080,
        }
    )
    config.update(
        {
            "REGISTRY_CACHE_NAME": "registry-lab",
            "REGISTRY_CACHE_ENDPOINT": "registry-lab:5000",
            "REGISTRY_CACHE_HOST_PORT": 50123,
            "REGISTRY_CACHE_DIR": str(tmp_path / "cache"),
        }
    )
    info_path = tmp_path / "runtime" / "info"

    start_module.write_info(config, info_path, "running", 32000)
    info = json.loads(info_path.read_text(encoding="utf-8"))

    assert info["schema_version"] == 2
    assert info["lab_name"] == "honeypotlab"
    assert info["cluster_target"] == "5Gcore"
    assert info["autoremove_lab"] is False
    assert info["controller_proxy"] == {
        "container_port": 18080,
        "exposed": True,
        "host_bind": "127.0.0.1",
        "host_port": 32000,
    }


@pytest.mark.unit
def test_extract_published_port_returns_first_numeric_port(start_module, monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(*_args, **_kwargs):
        return subprocess.CompletedProcess(args=[], returncode=0, stdout="127.0.0.1:32777\n", stderr="")

    monkeypatch.setattr(start_module, "run", fake_run)

    assert start_module.extract_published_port("honeypotlab-controller", 18080) == 32777


@pytest.mark.unit
def test_extract_published_port_returns_none_on_docker_failure(start_module, monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(*_args, **_kwargs):
        return subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="missing")

    monkeypatch.setattr(start_module, "run", fake_run)

    assert start_module.extract_published_port("missing", 18080) is None


@pytest.mark.unit
def test_valid_name_rejects_unsafe_docker_resource_names(start_module) -> None:
    start_module.valid_name("demo-5g_01", "LAB_NAME")
    with pytest.raises(SystemExit, match="Invalid LAB_NAME"):
        start_module.valid_name("../bad", "LAB_NAME")


@pytest.mark.unit
def test_autoremove_controller_uses_rm_without_restart_policy(start_module, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []
    config = start_module.prepare_config(
        {
            "LAB_NAME": "honeypotlab",
            "CLUSTER_TARGET": "5Gcore",
            "HOST_SOCKET": False,
            "EXPOSE_TO_HOST": True,
            "PROXY_BIND_ALL": False,
            "CONTROLLER_PROXY_CONTAINER_PORT": 18080,
            "AUTOREMOVE_LAB": True,
        }
    )

    def fake_run(args, **_kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(start_module, "run", fake_run)
    start_module.start_controller_container(config)

    docker_run = calls[0]
    assert "--rm" in docker_run
    assert "--restart" not in docker_run
