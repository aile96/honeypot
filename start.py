#!/usr/bin/env python3
"""Start a lab controller from typed TOML configuration."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
LIB_ROOT = PROJECT_ROOT / "src" / "lab" / "lib"
CONFIG_FILE = PROJECT_ROOT / "configuration.conf"
RESOURCE_CHECK_FILE = PROJECT_ROOT / "res" / "runtime" / ".resource-check"
RUNTIME_CONFIG_MOUNT = "/runtime"
RUNTIME_CONFIG_FILE = f"{RUNTIME_CONFIG_MOUNT}/config.toml"

# Runtime values owned by the host bootstrap.  Keep hardcoded values here only.
START_DEFAULTS: dict[str, object] = {
    # Shared Docker resources.
    "CONTROLLER_IMAGE": "lab-controller:latest",
    "CONTROLLER_PROXY_CONTAINER_PORT": 18080,
    "CP_NETWORK": "lab",
    "REGISTRY_CACHE_NAME": "registry-lab",
    "UNDERLAY_COMPOSE_WAIT_TIMEOUT_SECONDS": 120,
    # Kind defaults.
    "K8S_IMAGE": "kindest/node:v1.30.0",
    "KIND_POD_SUBNET": "10.244.0.0/16",
    "KIND_SERVICE_SUBNET": "10.96.0.0/12",
    "KIND_API_SERVER_ADDRESS": "127.0.0.1",
    "KIND_API_SERVER_PORT": "",
    # Controller entrypoint defaults.
    "DOCKER_SOCKET_PATH": "/var/run/docker.sock",
    "DOCKER_READY_TIMEOUT": 60,
    "FIRST_SCRIPT": "/app/start_lab.py",
    "SECOND_SCRIPT": "/app/start_caldera.py",
    "PROXY_SCRIPT": "/app/start_proxy.py",
    "IDLE_SLEEP_SECONDS": 3600,
    "CONFIG_FILE": RUNTIME_CONFIG_FILE,
    "ENV_FILE": RUNTIME_CONFIG_FILE,
    # In-container path defaults.
    "RES_DIR": "/res",
    "CODE_ROOT": "/workdir/code",
    "COMMON_CODE_ROOT": "/workdir/common",
    "PIPELINE_ROOT": "/app/pipeline",
}

if str(LIB_ROOT.parent) not in sys.path:
    sys.path.insert(0, str(LIB_ROOT.parent))

from lib import (  # noqa: E402
    ConfigError,
    DockerError,
    atomic_write_toml,
    check_docker,
    check_resources,
    container_exists,
    container_running,
    ensure_network,
    ensure_registry,
    host_socket_labs,
    load_project_config,
    run,
)


def log(message: str) -> None:
    print(f"[INFO] {message}", flush=True)


def fail(message: str) -> None:
    raise SystemExit(f"[ERR ] {message}")


def config_bool(config: dict[str, object], name: str, default: bool = False) -> bool:
    value = config.get(name, default)
    if isinstance(value, bool):
        return value
    raw = str(value).strip().lower()
    if raw in {"1", "true", "yes", "y", "on"}:
        return True
    if raw in {"0", "false", "no", "n", "off", ""}:
        return False
    fail(f"{name} must be a boolean value, got: {value!r}")


def valid_name(value: str, name: str) -> None:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", value):
        fail(f"Invalid {name}={value!r} for Docker resources")


def build_controller(config: dict[str, object]) -> None:
    image = str(config["CONTROLLER_IMAGE"])
    dockerfile = PROJECT_ROOT / "src" / "lab" / "lab-controller" / "Dockerfile"
    context = PROJECT_ROOT / "src"
    exists = run(["docker", "image", "inspect", image], check=False, quiet=True).returncode == 0
    if exists and not bool(config.get("BUILD_CONTROLLER", False)):
        log(f"Controller image {image!r} already exists; skipping build.")
        return
    log(f"Building controller image {image!r}.")
    run(["docker", "build", "--platform", "linux/amd64", "-t", image, "-f", str(dockerfile), str(context)])


def ensure_results_readable(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path, 0o755)
    except OSError:
        pass


def write_runtime_config(config: dict[str, object], path: Path) -> None:
    atomic_write_toml(path, {"config": config})


def write_info(config: dict[str, object], path: Path, status: str, host_port: int | None) -> None:
    proxy_exposed = host_port is not None
    info = {
        "schema_version": 2,
        "status": status,
        "lab_name": config["LAB_NAME"],
        "cluster_target": config["CLUSTER_TARGET"],
        "autoremove_lab": config_bool(config, "AUTOREMOVE_LAB", False),
        "host_socket": config["HOST_SOCKET"],
        "expose_to_host": config["EXPOSE_TO_HOST"],
        "controller_container": config["CONTROLLER_CONTAINER_NAME"],
        "network": config["CP_NETWORK"],
        "runtime_dir": config["RUNTIME_DIR"],
        "results_dir": config["RESULTS_DIR"],
        "host_runtime_dir": config["HOST_RUNTIME_DIR"],
        "host_results_dir": config["HOST_RESULTS_DIR"],
        "config_file": config["CONFIG_FILE"],
        "host_config_file": str(Path(str(config["HOST_RUNTIME_DIR"])) / "config.toml"),
        "state_file": config["STATE_FILE"],
        "registry": {
            "name": config.get("REGISTRY_CACHE_NAME"),
            "endpoint": config.get("REGISTRY_CACHE_ENDPOINT"),
            "host_port": config.get("REGISTRY_CACHE_HOST_PORT"),
            "cache_dir": config.get("REGISTRY_CACHE_DIR"),
        },
        "controller_proxy": {
            "container_port": config["CONTROLLER_PROXY_CONTAINER_PORT"],
            "host_bind": "0.0.0.0" if config["PROXY_BIND_ALL"] else "127.0.0.1",
            "host_port": host_port,
            "exposed": proxy_exposed,
        },
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(info, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def extract_published_port(container: str, container_port: int) -> int | None:
    completed = run(["docker", "port", container, f"{container_port}/tcp"], check=False, capture=True)
    if completed.returncode != 0:
        return None
    for line in completed.stdout.splitlines():
        _, _, port = line.rpartition(":")
        if port.isdigit():
            return int(port)
    return None


def start_controller_container(config: dict[str, object]) -> None:
    image = str(config["CONTROLLER_IMAGE"])
    host_socket = bool(config["HOST_SOCKET"])
    expose = bool(config["EXPOSE_TO_HOST"]) and not host_socket
    bind = "0.0.0.0" if bool(config["PROXY_BIND_ALL"]) else "127.0.0.1"
    port = int(config["CONTROLLER_PROXY_CONTAINER_PORT"])
    name = str(config["CONTROLLER_CONTAINER_NAME"])
    autoremove_lab = config_bool(config, "AUTOREMOVE_LAB", False)

    args = [
        "docker",
        "run",
        "-d",
        "--name",
        name,
        "--hostname",
        name,
        "--label",
        "honeypot.role=controller",
        "--network",
        str(config["CP_NETWORK"]),
        "--add-host",
        "host.docker.internal:host-gateway",
        "-v",
        f"{config['HOST_RES_DIR']}:{config['RES_DIR']}",
        "-v",
        f"{config['HOST_RUNTIME_DIR']}:{RUNTIME_CONFIG_MOUNT}",
        "-v",
        f"{config['HOST_CODE_ROOT']}:{config['CODE_ROOT']}:ro",
        "-v",
        f"{config['HOST_COMMON_CODE_ROOT']}:{config['COMMON_CODE_ROOT']}:ro",
    ]
    if autoremove_lab:
        args.append("--rm")
    else:
        args.extend(["--restart", "unless-stopped"])
    if not host_socket:
        args.extend(["--privileged", "--cgroupns=host"])
    if host_socket:
        socket_path = Path(str(config["DOCKER_SOCKET_PATH"]))
        if not socket_path.is_socket():
            fail(f"HOST_SOCKET=true but {socket_path} is not available")
        args.extend(["-v", f"{socket_path}:{socket_path}"])
    if expose:
        args.extend(["-p", f"{bind}::{port}"])
    args.append(image)
    log(f"Starting controller container {name!r}.")
    run(args, quiet=True)


def derived_start_defaults(lab_name: str, target: str) -> dict[str, object]:
    host_runtime_dir = PROJECT_ROOT / "res" / "runtime" / lab_name
    host_results_dir = PROJECT_ROOT / "res" / "results" / lab_name
    return {
        "CONTROLLER_CONTAINER_NAME": f"{lab_name}-controller",
        "COMPOSE_PROJECT_NAME": f"honeypot-{lab_name}",
        "KUBE_CONTEXT": f"kind-{lab_name}",
        "CLUSTER_PROFILE": lab_name,
        "HOST_RES_DIR": str(PROJECT_ROOT / "res"),
        "HOST_CODE_ROOT": str(PROJECT_ROOT / "src" / target),
        "HOST_COMMON_CODE_ROOT": str(PROJECT_ROOT / "src" / "common"),
        "HOST_RUNTIME_DIR": str(host_runtime_dir),
        "HOST_RESULTS_DIR": str(host_results_dir),
        "RUNTIME_DIR": f"/res/runtime/{lab_name}",
        "RESULTS_DIR": f"/res/results/{lab_name}",
        "GENERATED_DIR": f"/res/runtime/{lab_name}/generated",
        "STATE_FILE": f"/res/runtime/{lab_name}/generated/lab-state.json",
        "STATE_DIR": f"/res/runtime/{lab_name}/generated",
        "DOCKER_DATA_ROOT": f"/res/runtime/{lab_name}/docker-data",
        "HOOKS_DIR": "/workdir/code/hooks/pipeline",
        "CONTROLLER_HOOKS_DIR": "/workdir/code/hooks/controller",
        "CONTAINERS_ROOT": "/workdir/code/containers",
        "CALDERA_ROOT": "/workdir/code/caldera",
        "CALDERA_ADVERSARIES_DIR": "/workdir/code/caldera/adversaries",
        "HELM_CHARTS_ROOT": "/workdir/code/helm-charts",
    }


def prepare_config(raw: dict[str, object]) -> dict[str, object]:
    config = dict(raw)
    lab_name = str(config["LAB_NAME"])
    target = str(config["CLUSTER_TARGET"])
    config.update(START_DEFAULTS)
    config.update(derived_start_defaults(lab_name, target))
    config.setdefault("AUTOREMOVE_LAB", False)
    return config


def validate_paths(config: dict[str, object]) -> None:
    for key in ("HOST_CODE_ROOT", "HOST_COMMON_CODE_ROOT"):
        path = Path(str(config[key]))
        if not path.is_dir():
            fail(f"Required directory not found: {path}")
    for rel in ("conf-files/kind-cluster.yaml.tmpl", "conf-files/skaffold.yaml.tmpl", "conf-files/images.toml"):
        path = Path(str(config["HOST_CODE_ROOT"])) / rel
        if not path.is_file():
            fail(f"Required target file not found: {path}")
    compose_template = Path(str(config["HOST_CODE_ROOT"])) / "conf-files" / "compose.yaml.tmpl"
    if not compose_template.is_file():
        fail(f"Required compose template not found: {compose_template}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Start the honeypot lab controller")
    parser.add_argument("--config", default=str(CONFIG_FILE), help="Path to configuration.conf TOML file")
    parser.add_argument("--no-follow", action="store_true", help="Do not follow controller logs")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        raw = load_project_config(args.config)
        config = prepare_config(raw)
        valid_name(str(config["LAB_NAME"]), "LAB_NAME")
        valid_name(str(config["CONTROLLER_CONTAINER_NAME"]), "CONTROLLER_CONTAINER_NAME")
        validate_paths(config)
        check_docker()
        if not bool(config["SKIP_RESOURCE_CHECK"]):
            check_resources(config)
            RESOURCE_CHECK_FILE.parent.mkdir(parents=True, exist_ok=True)
            RESOURCE_CHECK_FILE.write_text("ok\n", encoding="utf-8")
        else:
            log("Skipping resource check.")

        runtime_root = PROJECT_ROOT / "res" / "runtime"
        conflicts = []
        if bool(config["HOST_SOCKET"]):
            for item in host_socket_labs(runtime_root, str(config["LAB_NAME"])):
                controller = str(item.get("controller_container", ""))
                if controller and container_running(controller):
                    conflicts.append(item)
        if conflicts:
            lab = conflicts[0].get("lab_name") or conflicts[0].get("host_runtime_dir")
            fail(f"Another HOST_SOCKET=true lab is already active: {lab}")
        if container_exists(str(config["CONTROLLER_CONTAINER_NAME"])):
            fail(f"Controller container {config['CONTROLLER_CONTAINER_NAME']!r} already exists")

        ensure_network(str(config["CP_NETWORK"]))
        registry = ensure_registry(config, PROJECT_ROOT, str(config["CP_NETWORK"]))
        config["REGISTRY_CACHE_NAME"] = registry["name"]
        config["REGISTRY_LAB_NAME"] = registry["name"]
        config["REGISTRY_CACHE_PORT"] = registry["port"]
        config["REGISTRY_LAB_PORT"] = registry["port"]
        config["REGISTRY_CACHE_HOST_PORT"] = registry["host_port"]
        config["REGISTRY_CACHE_ENDPOINT"] = registry["endpoint"]
        config["REGISTRY_CACHE_DIR"] = registry["cache_dir"]

        host_runtime = Path(str(config["HOST_RUNTIME_DIR"]))
        host_results = Path(str(config["HOST_RESULTS_DIR"]))
        host_runtime.mkdir(parents=True, exist_ok=True)
        ensure_results_readable(host_results)
        (host_runtime / "generated").mkdir(parents=True, exist_ok=True)
        write_runtime_config(config, host_runtime / "config.toml")
        write_info(config, host_runtime / "info", "starting", None)

        build_controller(config)
        start_controller_container(config)
        host_port = extract_published_port(str(config["CONTROLLER_CONTAINER_NAME"]), int(config["CONTROLLER_PROXY_CONTAINER_PORT"]))
        write_info(config, host_runtime / "info", "running", host_port)

        if host_port is None:
            log(f"Controller {config['CONTROLLER_CONTAINER_NAME']!r} started without host-published proxy.")
        else:
            bind = "0.0.0.0" if bool(config["PROXY_BIND_ALL"]) else "127.0.0.1"
            log(f"Controller {config['CONTROLLER_CONTAINER_NAME']!r} proxy exposed on {bind}:{host_port}.")

        if bool(config["FOLLOW_CONTROLLER_LOGS"]) and not args.no_follow:
            subprocess.run(["docker", "logs", "-f", str(config["CONTROLLER_CONTAINER_NAME"])], check=False)
        return 0
    except (ConfigError, DockerError, RuntimeError) as exc:
        fail(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
