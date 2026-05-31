"""Small Docker CLI helpers used by host bootstrap and cleanup."""

from __future__ import annotations

import json
import socket
import subprocess
from pathlib import Path
from typing import Any, Mapping


class DockerError(RuntimeError):
    """Raised when Docker is unavailable or an operation fails."""


def run(
    cmd: list[str],
    *,
    check: bool = True,
    capture: bool = False,
    quiet: bool = False,
    timeout: int | float | None = None,
) -> subprocess.CompletedProcess[str]:
    stdout = subprocess.PIPE if capture else subprocess.DEVNULL if quiet else None
    stderr = subprocess.PIPE if capture else subprocess.DEVNULL if quiet else None
    try:
        completed = subprocess.run(cmd, text=True, stdout=stdout, stderr=stderr, timeout=timeout, check=False)
    except FileNotFoundError as exc:
        raise DockerError(f"Required command not found: {cmd[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise DockerError(f"Command timed out after {timeout}s: {' '.join(cmd)}") from exc
    if check and completed.returncode != 0:
        details = ""
        if capture:
            details = (completed.stderr or completed.stdout or "").strip()
        raise DockerError(f"Command failed ({completed.returncode}): {' '.join(cmd)}{': ' + details if details else ''}")
    return completed


def check_docker() -> None:
    run(["docker", "version"], quiet=True)
    run(["docker", "ps"], quiet=True)


def container_exists(name: str) -> bool:
    return run(["docker", "container", "inspect", name], check=False, quiet=True).returncode == 0


def container_running(name: str) -> bool:
    completed = run(
        ["docker", "container", "inspect", "-f", "{{.State.Running}}", name],
        check=False,
        capture=True,
    )
    return completed.returncode == 0 and completed.stdout.strip().lower() == "true"


def ensure_network(name: str) -> None:
    if run(["docker", "network", "inspect", name], check=False, quiet=True).returncode == 0:
        return
    run(["docker", "network", "create", name], quiet=True)


def find_free_port(host: str = "127.0.0.1") -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        return int(sock.getsockname()[1])


def _container_json(name: str) -> dict[str, Any]:
    completed = run(["docker", "container", "inspect", name], capture=True)
    data = json.loads(completed.stdout)
    return data[0] if data else {}


def _published_host_port(name: str, container_port: int) -> int | None:
    completed = run(["docker", "port", name, f"{container_port}/tcp"], check=False, capture=True)
    if completed.returncode != 0:
        return None
    for line in completed.stdout.splitlines():
        _, _, port = line.rpartition(":")
        if port.isdigit():
            return int(port)
    return None


def image_id(image: str) -> str | None:
    completed = run(["docker", "image", "inspect", "-f", "{{.Id}}", image], check=False, capture=True)
    if completed.returncode != 0:
        return None
    return completed.stdout.strip() or None


def container_image_id(container: str) -> str | None:
    completed = run(["docker", "container", "inspect", "-f", "{{.Image}}", container], check=False, capture=True)
    if completed.returncode != 0:
        return None
    return completed.stdout.strip() or None


def active_lab_controllers(runtime_root: Path) -> list[str]:
    active: list[str] = []

    if not runtime_root.is_dir():
        return active

    for info in runtime_root.glob("*/info"):
        try:
            data = json.loads(info.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue

        controller = str(data.get("controller_container", "")).strip()
        if controller and container_running(controller):
            active.append(controller)

    return active


def ensure_registry(config: Mapping[str, Any], project_root: Path, network: str) -> dict[str, Any]:
    """Run the shared host cache registry when missing and return its endpoint."""
    name = str(config.get("REGISTRY_CACHE_NAME", "registry-lab"))
    image = str(config.get("REGISTRY_IMAGE", "lab-registry:latest"))
    port = int(config.get("REGISTRY_CACHE_PORT", 5000))
    host_bind = "127.0.0.1"
    cache_dir = project_root / "res" / "cache"
    runtime_root = project_root / "res" / "runtime"
    cache_dir.mkdir(parents=True, exist_ok=True)

    if container_exists(name):
        current_image = container_image_id(name)
        desired_image = image_id(image)

        if current_image and desired_image and current_image != desired_image:
            if active_lab_controllers(runtime_root):
                # Do not disrupt an existing lab that is still using the registry.
                # The registry will be recreated on the next start with no active controllers.
                pass
            else:
                stop_container(name, timeout=20)

    if container_exists(name):
        if not container_running(name):
            run(["docker", "start", name], quiet=True)
        run(["docker", "network", "connect", network, name], check=False, quiet=True)
    else:
        host_port = find_free_port(host_bind)
        run(
            [
                "docker",
                "run",
                "-d",
                "--name",
                name,
                "--restart",
                "unless-stopped",
                "--network",
                network,
                "-p",
                f"{host_bind}:{host_port}:{port}",
                "-v",
                f"{cache_dir}:/var/lib/registry",
                "-v",
                f"{runtime_root}:/runtime:ro",
                "-v",
                f"{config.get('DOCKER_SOCKET_PATH', '/var/run/docker.sock')}:/var/run/docker.sock",
                "-e",
                f"REGISTRY_CONTAINER_NAME={name}",
                "-e",
                "RUNTIME_ROOT=/runtime",
                "-e",
                f"REGISTRY_WATCHDOG_INTERVAL_SECONDS={config.get('REGISTRY_WATCHDOG_INTERVAL_SECONDS', 30)}",
                "-e",
                f"REGISTRY_EMPTY_GRACE_SECONDS={config.get('REGISTRY_EMPTY_GRACE_SECONDS', 180)}",
                image,
            ],
            quiet=True,
        )

    host_port = _published_host_port(name, port)
    if host_port is None:
        host_port = find_free_port(host_bind)
    endpoint = f"127.0.0.1:{host_port}" if bool(config.get("HOST_SOCKET", False)) else f"{name}:{port}"
    return {
        "name": name,
        "port": port,
        "host_port": host_port,
        "endpoint": endpoint,
        "cache_dir": str(cache_dir),
    }


def host_socket_labs(runtime_root: Path, current_lab: str) -> list[dict[str, Any]]:
    labs: list[dict[str, Any]] = []
    if not runtime_root.is_dir():
        return labs
    for info in runtime_root.glob("*/info"):
        if info.parent.name == current_lab:
            continue
        try:
            data = json.loads(info.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if data.get("host_socket"):
            labs.append(data)
    return labs


def stop_container(name: str, timeout: int = 60) -> None:
    if not container_exists(name):
        return
    run(["docker", "stop", "--time", str(timeout), name], check=False, quiet=True, timeout=timeout + 5)
    run(["docker", "rm", "-f", name], check=False, quiet=True)


def remove_registry_if_unused(project_root: Path, registry_name: str = "registry-lab") -> None:
    runtime_root = project_root / "res" / "runtime"
    if not active_lab_controllers(runtime_root) and container_exists(registry_name):
        stop_container(registry_name, timeout=20)


def network_exists(name: str) -> bool:
    return run(["docker", "network", "inspect", name], check=False, quiet=True).returncode == 0


def remove_network_if_unused(project_root: Path, network_name: str = "lab") -> None:
    runtime_root = project_root / "res" / "runtime"
    if not active_lab_controllers(runtime_root) and network_exists(network_name):
        run(["docker", "network", "rm", network_name], check=False, quiet=True)
