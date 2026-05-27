#!/usr/bin/env python3
"""Render, build, and deploy the target Skaffold stack."""

import json
import re
import shutil
from pathlib import Path

from lib import (
    config_bool,
    config_str,
    docker_login_internal_registry,
    get_state_value,
    generated_dir,
    kube_context_name,
    log,
    registry_endpoint,
    render_skaffold_config,
    run_cmd,
    skaffold_artifacts_path,
    skaffold_workdir,
    set_state_value,
)


ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def skaffold_has_build_artifacts(skaffold_file: Path) -> bool:
    """Return True when the rendered Skaffold file declares build artifacts."""
    text = skaffold_file.read_text(encoding="utf-8")
    return (
        re.search(r"(?m)^build:\s*$", text) is not None
        and re.search(r"(?m)^\s*artifacts:\s*$", text) is not None
        and re.search(r"(?m)^\s*-\s*image:", text) is not None
    )


def skaffold_has_deploy_config(skaffold_file: Path) -> bool:
    """Return True when the rendered Skaffold file declares a deploy section."""
    text = skaffold_file.read_text(encoding="utf-8")
    return re.search(r"(?m)^deploy:\s*$", text) is not None


def skaffold_cli_config_path(skaffold_file: Path, workdir: Path) -> str:
    try:
        return str(skaffold_file.relative_to(workdir))
    except ValueError:
        return str(skaffold_file)


def skaffold_env_from_hook() -> dict[str, str]:
    """Return Skaffold env prepared by HOOK_PRE_04."""
    env = get_state_value(STATE, "skaffold_render_env", None)

    if not isinstance(env, dict):
        raise SystemExit("Missing STATE['skaffold_render_env']; prepare it in HOOK_PRE_04.py.")

    return {str(key): str(value) for key, value in env.items()}


def prepare_writable_helm_charts(config: dict, env: dict[str, str]) -> Path | None:
    """Copy target Helm charts to runtime so Skaffold/Helm can write temp files."""
    source = Path(str(config.get("HELM_CHARTS_ROOT") or skaffold_workdir(config) / "helm-charts"))
    if not source.is_dir():
        return None

    destination = generated_dir(config) / "helm-charts"
    if source.resolve() != destination.resolve():
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            shutil.rmtree(destination)
        shutil.copytree(
            source,
            destination,
            symlinks=True,
            ignore=shutil.ignore_patterns("tmpcharts-*", "__pycache__"),
        )

    config["HELM_CHARTS_ROOT"] = str(destination)
    env["HELM_CHARTS_ROOT"] = str(destination)
    log(f"Prepared writable Helm charts at {destination}.")
    return destination


def render_skaffold(skaffold_env: dict[str, str]) -> Path:
    """Render Skaffold config."""
    skaffold_file, skaffold_template = render_skaffold_config(CONFIG, skaffold_env)

    set_state_value(STATE, "skaffold_config", str(skaffold_file))
    if skaffold_template is not None:
        set_state_value(STATE, "skaffold_template_config", str(skaffold_template))

    return skaffold_file


def use_skaffold_build_helper(config: dict) -> bool:
    """Return whether Skaffold build should run in a DinD helper."""
    default = config_bool(config, "HOST_SOCKET", False)
    return config_bool(config, "SKAFFOLD_BUILD_HELPER_ENABLED", default)


def skaffold_render_env(config: dict, skaffold_env: dict[str, str]) -> dict[str, str]:
    """Return the render env for the active Docker runtime."""
    env = dict(skaffold_env)
    if use_skaffold_build_helper(config):
        env["CP_NETWORK"] = config_str(
            config,
            "SKAFFOLD_BUILD_HELPER_BUILD_NETWORK",
            "host",
            allow_empty=False,
        )
    return env


def built_images_from_artifacts(artifact_file: Path) -> list[str]:
    """Return image tags emitted by `skaffold build --file-output`."""
    if not artifact_file.is_file():
        return []
    try:
        data = json.loads(artifact_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    builds = data.get("builds", [])
    if not isinstance(builds, list):
        return []
    images: list[str] = []
    for item in builds:
        if isinstance(item, dict) and item.get("tag"):
            images.append(str(item["tag"]))
    return images


def skaffold_insecure_registry_args(config: dict) -> list[str]:
    """Return CLI flags for every HTTP registry Skaffold must contact."""
    lab_registry = (
        f"{config_str(config, 'REGISTRY_LAB_NAME', 'registry-lab', allow_empty=False)}:"
        f"{config_str(config, 'REGISTRY_LAB_PORT', '5000', allow_empty=False)}"
    )
    endpoints = [
        registry_endpoint(config),
        lab_registry,
        config_str(config, "REGISTRY_CACHE_ENDPOINT", "registry-lab:5000", allow_empty=False),
    ]
    args: list[str] = []
    seen: set[str] = set()
    for endpoint in endpoints:
        endpoint = endpoint.strip()
        if endpoint and endpoint not in seen:
            args.extend(["--insecure-registry", endpoint])
            seen.add(endpoint)
    return args


def helper_insecure_registries(config: dict) -> list[str]:
    """Return registries the helper dockerd should treat as HTTP/insecure."""
    endpoints = [
        (
            f"{config_str(config, 'REGISTRY_LAB_NAME', 'registry-lab', allow_empty=False)}:"
            f"{config_str(config, 'REGISTRY_LAB_PORT', '5000', allow_empty=False)}"
        ),
        config_str(config, "REGISTRY_CACHE_ENDPOINT", "", allow_empty=True),
    ]
    registries: list[str] = []
    seen: set[str] = set()
    for endpoint in endpoints:
        endpoint = endpoint.strip()
        if not endpoint or endpoint in seen:
            continue
        if endpoint.startswith(("127.0.0.1:", "localhost:")):
            continue
        registries.append(endpoint)
        seen.add(endpoint)
    return registries


def write_skaffold_helper_script(config: dict) -> Path:
    """Write the script that starts helper dockerd and then runs Skaffold."""
    script = generated_dir(config) / "skaffold-build-helper.sh"
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text(
        """#!/bin/sh
set -eu

socket="${SKAFFOLD_HELPER_DOCKER_SOCKET:-/run/skaffold-docker/docker.sock}"
data_root="${SKAFFOLD_HELPER_DOCKER_DATA_ROOT:-/var/lib/skaffold-docker}"
exec_root="${SKAFFOLD_HELPER_DOCKER_EXEC_ROOT:-/var/run/skaffold-docker/exec}"
pidfile="${SKAFFOLD_HELPER_DOCKER_PIDFILE:-/var/run/skaffold-docker/docker.pid}"
log_file="${SKAFFOLD_HELPER_DOCKER_LOG:-/tmp/skaffold-helper-dockerd.log}"
registry_hostport="${REGISTRY_NAME:-registry}:${REGISTRY_PORT:-5000}"

mkdir -p "$(dirname "$socket")" "$data_root" "$exec_root" "$(dirname "$pidfile")" "$(dirname "$log_file")"
rm -f "$socket" "$pidfile"

if [ -n "${REGISTRY_CA_FILE:-}" ] && [ -f "$REGISTRY_CA_FILE" ]; then
    mkdir -p "/etc/docker/certs.d/$registry_hostport" /usr/local/share/ca-certificates
    cp "$REGISTRY_CA_FILE" "/etc/docker/certs.d/$registry_hostport/ca.crt"
    cp "$REGISTRY_CA_FILE" /usr/local/share/ca-certificates/lab-registry.crt
    update-ca-certificates >/dev/null 2>&1 || true
fi

insecure_args=""
for registry in ${SKAFFOLD_HELPER_INSECURE_REGISTRIES:-}; do
    insecure_args="$insecure_args --insecure-registry=$registry"
done

echo "[helper] starting internal dockerd"
dockerd \
    "--host=unix://$socket" \
    "--data-root=$data_root" \
    "--exec-root=$exec_root" \
    "--pidfile=$pidfile" \
    $insecure_args >"$log_file" 2>&1 &
dockerd_pid="$!"

cleanup() {
    status="$?"
    if kill -0 "$dockerd_pid" >/dev/null 2>&1; then
        kill "$dockerd_pid" >/dev/null 2>&1 || true
        wait "$dockerd_pid" >/dev/null 2>&1 || true
    fi
    if [ "$status" -ne 0 ] && [ -f "$log_file" ]; then
        echo "========== helper dockerd log tail ==========" >&2
        tail -n 200 "$log_file" >&2 || true
        echo "======== end helper dockerd log tail ========" >&2
    fi
    exit "$status"
}
trap cleanup EXIT INT TERM

deadline="${SKAFFOLD_HELPER_DOCKER_READY_TIMEOUT:-90}"
i=0
while [ "$i" -lt "$deadline" ]; do
    if DOCKER_HOST="unix://$socket" docker info >/dev/null 2>&1; then
        break
    fi
    if ! kill -0 "$dockerd_pid" >/dev/null 2>&1; then
        echo "[helper] dockerd exited before becoming ready" >&2
        exit 1
    fi
    i=$((i + 1))
    sleep 1
done

if ! DOCKER_HOST="unix://$socket" docker info >/dev/null 2>&1; then
    echo "[helper] dockerd did not become ready within ${deadline}s" >&2
    exit 1
fi

export DOCKER_HOST="unix://$socket"
export DOCKER_SOCKET_PATH="$socket"

if [ -n "${REGISTRY_USER:-}" ]; then
    printf '%s\\n' "${REGISTRY_PASS:-}" | docker login "$registry_hostport" -u "$REGISTRY_USER" --password-stdin
fi

echo "[helper] running: $*"
set +e
"$@"
command_status="$?"
set -e
exit "$command_status"
""",
        encoding="utf-8",
    )
    script.chmod(0o755)
    return script


def write_skaffold_helper_env(config: dict, skaffold_env: dict[str, str]) -> Path:
    """Write an env-file consumed by the helper container."""
    env = dict(skaffold_env)
    env.pop("DOCKER_HOST", None)
    env.pop("DOCKER_SOCKET_PATH", None)
    env["SKAFFOLD_HELPER_DOCKER_SOCKET"] = "/run/skaffold-docker/docker.sock"
    env["SKAFFOLD_HELPER_DOCKER_DATA_ROOT"] = config_str(
        config,
        "SKAFFOLD_HELPER_DOCKER_DATA_ROOT",
        "/var/lib/skaffold-docker",
        allow_empty=False,
    )
    env["SKAFFOLD_HELPER_DOCKER_EXEC_ROOT"] = "/var/run/skaffold-docker/exec"
    env["SKAFFOLD_HELPER_DOCKER_PIDFILE"] = "/var/run/skaffold-docker/docker.pid"
    env["SKAFFOLD_HELPER_DOCKER_LOG"] = config_str(
        config,
        "SKAFFOLD_HELPER_DOCKER_LOG",
        "/tmp/skaffold-helper-dockerd.log",
        allow_empty=False,
    )
    env["SKAFFOLD_HELPER_INSECURE_REGISTRIES"] = " ".join(helper_insecure_registries(config))

    env_file = generated_dir(config) / "skaffold-build-helper.env"
    env_file.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    for name in sorted(env):
        value = str(env[name])
        if not ENV_NAME_RE.match(name) or "\n" in value:
            continue
        lines.append(f"{name}={value}")
    env_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return env_file


def helper_mount_args(config: dict) -> list[str]:
    """Return Docker mount args for the Skaffold build helper."""
    mounts = [
        (
            config_str(config, "HOST_RES_DIR", "", allow_empty=False),
            config_str(config, "RES_DIR", "/res", allow_empty=False),
            "",
        ),
        (
            config_str(config, "HOST_CODE_ROOT", "", allow_empty=False),
            config_str(config, "CODE_ROOT", "/workdir/code", allow_empty=False),
            "ro",
        ),
        (
            config_str(config, "HOST_COMMON_CODE_ROOT", "", allow_empty=False),
            config_str(config, "COMMON_CODE_ROOT", "/workdir/common", allow_empty=False),
            "ro",
        ),
        (
            config_str(config, "HOST_RUNTIME_DIR", "", allow_empty=False),
            "/runtime",
            "",
        ),
    ]

    args: list[str] = []
    for source, destination, mode in mounts:
        spec = f"{source}:{destination}"
        if mode:
            spec = f"{spec}:{mode}"
        args.extend(["-v", spec])
    return args


def run_skaffold_build_in_helper(
    skaffold_file: Path,
    skaffold_env: dict[str, str],
    command: list[str],
) -> None:
    """Run Skaffold build inside a short-lived helper with its own dockerd."""
    helper_script = write_skaffold_helper_script(CONFIG)
    helper_env = write_skaffold_helper_env(CONFIG, skaffold_env)
    helper_name = config_str(
        CONFIG,
        "SKAFFOLD_BUILD_HELPER_NAME",
        f"{config_str(CONFIG, 'LAB_NAME', 'honeypotlab', allow_empty=False)}-skaffold-build",
        allow_empty=False,
    )
    image = config_str(
        CONFIG,
        "SKAFFOLD_BUILD_HELPER_IMAGE",
        config_str(CONFIG, "CONTROLLER_IMAGE", "lab-controller:latest"),
        allow_empty=False,
    )
    workdir = skaffold_workdir(CONFIG)

    run_cmd(["docker", "rm", "-f", helper_name], check=False, quiet=True, config=CONFIG)

    docker_run = [
        "docker",
        "run",
        "--rm",
        "--privileged",
        "--cgroupns=host",
        "--name",
        helper_name,
        "--hostname",
        helper_name,
        "--label",
        "honeypot.role=skaffold-build-helper",
        "--label",
        f"honeypot.lab={config_str(CONFIG, 'LAB_NAME', 'honeypotlab', allow_empty=False)}",
        "--network",
        config_str(CONFIG, "CP_NETWORK", "lab", allow_empty=False),
        "--add-host",
        "host.docker.internal:host-gateway",
        "--env-file",
        str(helper_env),
        *helper_mount_args(CONFIG),
        "-w",
        str(workdir),
        "--entrypoint",
        "/bin/sh",
        image,
        str(helper_script),
        *command,
    ]

    set_state_value(
        STATE,
        "skaffold_build_helper",
        {
            "enabled": True,
            "container": helper_name,
            "image": image,
            "script": str(helper_script),
            "env_file": str(helper_env),
            "skaffold_file": str(skaffold_file),
        },
    )
    log(f"Running Skaffold build in helper container {helper_name!r}.")
    run_cmd(docker_run, config=CONFIG)


def build_skaffold(skaffold_file: Path, skaffold_env: dict[str, str]) -> list[str]:
    """Run Skaffold build using the rendered Skaffold config."""
    artifact_file = skaffold_artifacts_path(CONFIG)
    artifact_file.unlink(missing_ok=True)

    if not skaffold_has_build_artifacts(skaffold_file):
        log("Skipping Skaffold build because no build artifacts are declared.")
        set_state_value(STATE, "skaffold_build_artifacts", "")
        set_state_value(STATE, "cluster_images", [])
        set_state_value(STATE, "pushed_cluster_images", [])
        return []

    workdir = skaffold_workdir(CONFIG)
    command = [
        "skaffold",
        "build",
        "-f",
        skaffold_cli_config_path(skaffold_file, workdir),
        "--file-output",
        str(artifact_file),
        *skaffold_insecure_registry_args(CONFIG),
    ]

    if use_skaffold_build_helper(CONFIG):
        run_skaffold_build_in_helper(skaffold_file, skaffold_env, command)
    else:
        docker_login_internal_registry(CONFIG)
        run_cmd(command, config=CONFIG, env=skaffold_env, cwd=workdir)
    images = built_images_from_artifacts(artifact_file)

    set_state_value(STATE, "skaffold_build_artifacts", str(artifact_file))
    set_state_value(STATE, "cluster_images", images)
    set_state_value(STATE, "pushed_cluster_images", images)

    return images


def deploy_skaffold(skaffold_file: Path, skaffold_env: dict[str, str]) -> None:
    """Run Skaffold deploy."""
    if not skaffold_has_deploy_config(skaffold_file):
        log("Skipping Skaffold deploy because no deploy section is declared.")
        return

    workdir = skaffold_workdir(CONFIG)

    command = [
        "skaffold",
        "deploy",
        "-f",
        skaffold_cli_config_path(skaffold_file, workdir),
        "--kube-context",
        kube_context_name(CONFIG),
    ]

    artifact_file = skaffold_artifacts_path(CONFIG)
    if artifact_file.is_file():
        command.extend(["--build-artifacts", str(artifact_file)])

    run_cmd(
        command,
        config=CONFIG,
        env=skaffold_env,
        cwd=workdir,
    )

    set_state_value(STATE, "skaffold_config", str(skaffold_file))
    if artifact_file.is_file():
        set_state_value(STATE, "skaffold_build_artifacts", str(artifact_file))


def main() -> None:
    skaffold_env = skaffold_render_env(CONFIG, skaffold_env_from_hook())
    writable_helm_charts = prepare_writable_helm_charts(CONFIG, skaffold_env)
    if writable_helm_charts is not None:
        set_state_value(STATE, "helm_charts_root", str(writable_helm_charts))

    skaffold_file = render_skaffold(skaffold_env)
    build_skaffold(skaffold_file, skaffold_env)
    deploy_skaffold(skaffold_file, skaffold_env)

    set_state_value(STATE, "skaffold_build_detected", skaffold_has_build_artifacts(skaffold_file))
    set_state_value(STATE, "skaffold_deploy_detected", skaffold_has_deploy_config(skaffold_file))

    log("Skaffold render, build, and deploy completed.")


if __name__ == "__main__":
    main()
