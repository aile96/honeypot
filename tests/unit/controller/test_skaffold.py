from __future__ import annotations

from pathlib import Path

import pytest


@pytest.mark.unit
def test_prepare_writable_helm_charts_copies_to_generated_dir(controller_importer, tmp_path: Path) -> None:
    skaffold = controller_importer.script(
        "skaffold_step",
        Path("src/lab/lab-controller/app/pipeline/04_skaffold.py").resolve(),
    )
    source = tmp_path / "code" / "helm-charts"
    (source / "telemetry" / "tmpcharts-old").mkdir(parents=True)
    (source / "telemetry" / "Chart.yaml").write_text("apiVersion: v2\nname: telemetry\n", encoding="utf-8")
    (source / "telemetry" / "tmpcharts-old" / "stale.txt").write_text("stale\n", encoding="utf-8")

    config = {
        "CODE_ROOT": str(tmp_path / "code"),
        "GENERATED_DIR": str(tmp_path / "runtime" / "generated"),
        "HELM_CHARTS_ROOT": str(source),
    }
    env: dict[str, str] = {}

    destination = skaffold.prepare_writable_helm_charts(config, env)

    assert destination == tmp_path / "runtime" / "generated" / "helm-charts"
    assert (destination / "telemetry" / "Chart.yaml").is_file()
    assert not (destination / "telemetry" / "tmpcharts-old").exists()
    assert config["HELM_CHARTS_ROOT"] == str(destination)
    assert env["HELM_CHARTS_ROOT"] == str(destination)


@pytest.mark.unit
def test_skaffold_insecure_registry_args_include_target_and_cache(controller_importer) -> None:
    skaffold = controller_importer.script(
        "skaffold_step_args",
        Path("src/lab/lab-controller/app/pipeline/04_skaffold.py").resolve(),
    )

    assert skaffold.skaffold_insecure_registry_args(
        {
            "REGISTRY_NAME": "registry",
            "REGISTRY_PORT": 5000,
            "REGISTRY_LAB_NAME": "registry-lab",
            "REGISTRY_LAB_PORT": 5000,
            "REGISTRY_CACHE_ENDPOINT": "registry-lab:5000",
        }
    ) == [
        "--insecure-registry",
        "registry:5000",
        "--insecure-registry",
        "registry-lab:5000",
    ]

    assert skaffold.skaffold_insecure_registry_args(
        {
            "REGISTRY_NAME": "registry",
            "REGISTRY_PORT": 5000,
            "REGISTRY_LAB_NAME": "registry",
            "REGISTRY_LAB_PORT": 5000,
            "REGISTRY_CACHE_ENDPOINT": "registry:5000",
        }
    ) == ["--insecure-registry", "registry:5000"]


@pytest.mark.unit
def test_skaffold_render_env_uses_host_network_for_helper(controller_importer) -> None:
    skaffold = controller_importer.script(
        "skaffold_step_render_env",
        Path("src/lab/lab-controller/app/pipeline/04_skaffold.py").resolve(),
    )

    env = skaffold.skaffold_render_env(
        {"HOST_SOCKET": True, "SKAFFOLD_BUILD_HELPER_ENABLED": True},
        {"CP_NETWORK": "lab", "IMAGE_VERSION": "2.0.2"},
    )

    assert env["CP_NETWORK"] == "host"
    assert env["IMAGE_VERSION"] == "2.0.2"


@pytest.mark.unit
def test_helper_env_uses_lab_registry_and_omits_host_socket(controller_importer, tmp_path: Path) -> None:
    skaffold = controller_importer.script(
        "skaffold_step_helper_env",
        Path("src/lab/lab-controller/app/pipeline/04_skaffold.py").resolve(),
    )
    config = {
        "GENERATED_DIR": str(tmp_path / "generated"),
        "RUNTIME_DIR": str(tmp_path / "runtime"),
        "REGISTRY_LAB_NAME": "registry-lab",
        "REGISTRY_LAB_PORT": 5000,
        "REGISTRY_CACHE_ENDPOINT": "127.0.0.1:48000",
    }

    env_file = skaffold.write_skaffold_helper_env(
        config,
        {
            "DOCKER_HOST": "unix:///var/run/docker.sock",
            "DOCKER_SOCKET_PATH": "/var/run/docker.sock",
            "IMAGE_VERSION": "2.0.2",
        },
    )

    content = env_file.read_text(encoding="utf-8")
    assert "DOCKER_HOST=" not in content
    assert "DOCKER_SOCKET_PATH=" not in content
    assert "IMAGE_VERSION=2.0.2" in content
    assert "SKAFFOLD_HELPER_INSECURE_REGISTRIES=registry-lab:5000" in content


@pytest.mark.unit
def test_build_skaffold_runs_in_helper_for_host_socket(
    controller_importer,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    skaffold = controller_importer.script(
        "skaffold_step_helper_build",
        Path("src/lab/lab-controller/app/pipeline/04_skaffold.py").resolve(),
    )
    runtime = tmp_path / "runtime"
    generated = runtime / "generated"
    code = tmp_path / "code"
    common = tmp_path / "common"
    res = tmp_path / "res"
    for path in (generated, code, common, res):
        path.mkdir(parents=True, exist_ok=True)
    skaffold_file = generated / "skaffold.yaml"
    skaffold_file.write_text(
        "build:\n"
        "  artifacts:\n"
        "  - image: registry:5000/demo\n",
        encoding="utf-8",
    )
    artifact_file = generated / "skaffold-build-artifacts.json"

    skaffold.CONFIG = {
        "HOST_SOCKET": True,
        "SKAFFOLD_BUILD_HELPER_ENABLED": True,
        "LAB_NAME": "lab",
        "CP_NETWORK": "lab",
        "CONTROLLER_IMAGE": "lab-controller:latest",
        "GENERATED_DIR": str(generated),
        "RUNTIME_DIR": str(runtime),
        "HOST_RUNTIME_DIR": str(tmp_path / "host-runtime"),
        "HOST_RES_DIR": str(res),
        "RES_DIR": "/res",
        "HOST_CODE_ROOT": str(code),
        "CODE_ROOT": "/workdir/code",
        "HOST_COMMON_CODE_ROOT": str(common),
        "COMMON_CODE_ROOT": "/workdir/common",
        "REGISTRY_NAME": "registry",
        "REGISTRY_PORT": 5000,
        "REGISTRY_LAB_NAME": "registry-lab",
        "REGISTRY_LAB_PORT": 5000,
        "REGISTRY_CACHE_ENDPOINT": "127.0.0.1:48000",
    }
    skaffold.STATE = {}

    calls = []

    def fake_run_cmd(cmd, **kwargs):
        calls.append((cmd, kwargs))
        if cmd[:2] == ["docker", "run"]:
            artifact_file.write_text(
                '{"builds":[{"tag":"registry:5000/demo:2.0.2@sha256:abc"}]}\n',
                encoding="utf-8",
            )
        import subprocess

        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(skaffold, "run_cmd", fake_run_cmd)
    monkeypatch.setattr(
        skaffold,
        "docker_login_internal_registry",
        lambda _config: pytest.fail("host-socket helper should perform docker login inside the helper"),
    )

    assert skaffold.build_skaffold(skaffold_file, {"IMAGE_VERSION": "2.0.2"}) == [
        "registry:5000/demo:2.0.2@sha256:abc"
    ]

    docker_run = [cmd for cmd, _kwargs in calls if cmd[:2] == ["docker", "run"]][0]
    assert "--privileged" in docker_run
    assert "--network" in docker_run
    assert docker_run[docker_run.index("--network") + 1] == "lab"
    assert "--env-file" in docker_run
    assert docker_run[docker_run.index("--env-file") + 1] == str(
        generated / "skaffold-build-helper.env"
    )
    assert docker_run[docker_run.index("--entrypoint") + 1] == "/bin/sh"
    assert "lab-controller:latest" in docker_run
    assert "skaffold" in docker_run
    assert "build" in docker_run
