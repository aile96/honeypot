from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


@pytest.mark.unit
def test_docker_build_disables_buildkit_for_custom_lab_network(controller_importer, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    images = controller_importer.module("lib.images")
    captured = {}

    def fake_run_cmd(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["env"] = kwargs.get("env")
        captured["config"] = kwargs.get("config")
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(images, "run_cmd", fake_run_cmd)

    images.docker_build(
        "registry:5000/demo:latest",
        tmp_path,
        tmp_path / "Dockerfile",
        {"ARG": "value"},
        {"CP_NETWORK": "lab", "DOCKER_BUILD_TIMEOUT_SECONDS": 0},
    )

    assert captured["config"] is None
    assert captured["env"]["DOCKER_BUILDKIT"] == "0"
    assert captured["cmd"][:5] == ["docker", "build", "--pull=false", "--no-cache", "--network"]
    assert captured["cmd"][5] == "lab"


@pytest.mark.unit
def test_build_cache_image_uses_buildkit_without_custom_network(
    controller_importer,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    images = controller_importer.module("lib.images")
    calls = []
    image = {"name": "demo", "context": ".", "tag": "${IMAGE_VERSION}", "targets": ["skaffold"]}
    config = {
        "CLUSTER_TARGET": "demo",
        "TARGET_CODE_ROOT": str(tmp_path),
        "REGISTRY_CACHE_ENDPOINT": "127.0.0.1:48000",
        "IMAGE_VERSION": "2.0.2",
        "CP_NETWORK": "lab",
    }

    monkeypatch.setattr(images, "registry_has_image", lambda ref: False)

    def fake_docker_build(ref, context, dockerfile, args, config, **kwargs):
        calls.append((ref, kwargs))

    monkeypatch.setattr(images, "docker_build", fake_docker_build)

    assert images.build_cache_image(config, image) == "127.0.0.1:48000/demo:2.0.2"
    assert calls == [("127.0.0.1:48000/demo:2.0.2", {"buildkit": True, "network": ""})]


@pytest.mark.unit
def test_build_cache_image_rebuilds_cached_image_when_validator_fails(
    controller_importer,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    images = controller_importer.module("lib.images")
    calls = []
    validation_results = [False, True]
    image = {"name": "ad", "context": ".", "tag": "1.0.1", "validators": ["javaagent"]}
    config = {
        "CLUSTER_TARGET": "demo",
        "TARGET_CODE_ROOT": str(tmp_path),
        "REGISTRY_CACHE_ENDPOINT": "127.0.0.1:48000",
        "CP_NETWORK": "lab",
    }

    monkeypatch.setattr(images, "registry_has_image", lambda ref: True)
    monkeypatch.setattr(images, "validate_cache_image", lambda ref, image, config: validation_results.pop(0))

    def fake_docker_build(ref, context, dockerfile, args, config, **kwargs):
        calls.append((ref, kwargs))

    monkeypatch.setattr(images, "docker_build", fake_docker_build)

    assert images.build_cache_image(config, image) == "127.0.0.1:48000/ad:1.0.1"
    assert calls == [("127.0.0.1:48000/ad:1.0.1", {"buildkit": True, "network": ""})]
    assert validation_results == []


@pytest.mark.unit
def test_build_cache_image_skips_cached_image_when_validator_passes(
    controller_importer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    images = controller_importer.module("lib.images")
    image = {"name": "ad", "context": ".", "tag": "1.0.1", "validators": ["javaagent"]}

    monkeypatch.setattr(images, "registry_has_image", lambda ref: True)
    monkeypatch.setattr(images, "validate_cache_image", lambda ref, image, config: True)
    monkeypatch.setattr(images, "docker_build", lambda *args, **kwargs: pytest.fail("docker_build should not run"))

    assert images.build_cache_image({"REGISTRY_CACHE_ENDPOINT": "127.0.0.1:48000"}, image) == ""


@pytest.mark.unit
def test_validate_javaagent_image_runs_java_as_nonroot(controller_importer, monkeypatch: pytest.MonkeyPatch) -> None:
    images = controller_importer.module("lib.images")
    captured = {}

    def fake_run_cmd(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="opentelemetry-javaagent - version: 2.15.0")

    monkeypatch.setattr(images, "run_cmd", fake_run_cmd)

    assert images.validate_javaagent_image("registry-lab:5000/ad:1.0.1", {}) is True
    assert captured["cmd"] == [
        "docker",
        "run",
        "--rm",
        "--user",
        "1000",
        "--entrypoint",
        "java",
        "-e",
        "OTEL_SDK_DISABLED=true",
        "registry-lab:5000/ad:1.0.1",
        "-version",
    ]
    assert captured["kwargs"]["check"] is False
    assert captured["kwargs"]["capture_output"] is True


@pytest.mark.unit
def test_cache_registry_endpoint_reads_config_or_default(controller_importer) -> None:
    images = controller_importer.module("lib.images")

    assert images.cache_registry_endpoint({}) == "registry-lab:5000"
    assert images.cache_registry_endpoint({"REGISTRY_CACHE_ENDPOINT": "127.0.0.1:48000"}) == "127.0.0.1:48000"


@pytest.mark.unit
def test_cache_image_ref_uses_per_image_tag_instead_of_global_image_version(controller_importer) -> None:
    images = controller_importer.module("lib.images")
    image = {"name": "demo", "context": ".", "tag": "1.0.1"}
    config = {"REGISTRY_CACHE_ENDPOINT": "127.0.0.1:48000", "IMAGE_VERSION": "2.0.2"}

    assert images.cache_image_ref(config, image) == "127.0.0.1:48000/demo:1.0.1"
