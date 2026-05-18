#!/usr/bin/env python3
"""Provide Docker container, image, and bind-mount utilities.

The helpers hide small Docker CLI details used throughout hooks and pipeline
steps, including container discovery, IP lookup, image checks, safe removal, and
conversion from controller-container paths to host bind-mount paths."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from .command import run_cmd
from .config import Config, config_bool, config_str
from .logging import log

HONEYPOT_DOCKER_IP_CACHE: dict[str, str] = {}
HONEYPOT_DOCKER_IP_CACHE_READY = False


def is_running_in_container() -> bool:
    """Return True when running inside a container."""
    return Path("/.dockerenv").exists() or Path("/run/.containerenv").exists()


def docker_bind_source(path: Path, config: Mapping[str, Any] | None = None) -> Path:
    """Resolve a path for Docker bind mounts.

    In host-socket mode Docker sees host paths, not container paths. This maps
    known container mount prefixes back to their host equivalents using CONFIG.
    """
    source = path.resolve()

    if config is None or not config_bool(config, "HOST_SOCKET", False):
        return source

    dynamic_mappings = [
        ("DOCKER_DATA_ROOT", "HOST_CONTROLLER_DOCKER_DATA_DIR"),
        ("BUILD_HELPER_CACHE_DIR", "HOST_BUILD_HELPER_CACHE_DIR"),
        ("RUNTIME_DIR", "HOST_RUNTIME_DIR"),
        ("RESULTS_DIR", "HOST_CONTROLLER_RESULTS_DIR"),
    ]
    for container_config_name, host_config_name in dynamic_mappings:
        container_prefix_raw = config_str(config, container_config_name, "").strip()
        host_prefix_raw = config_str(config, host_config_name, "").strip()
        if not container_prefix_raw or not host_prefix_raw:
            continue

        container_prefix = Path(container_prefix_raw).resolve()
        host_prefix = Path(host_prefix_raw).resolve()
        try:
            relative = source.relative_to(container_prefix)
        except ValueError:
            continue

        return (host_prefix / relative).resolve()

    mount_mappings = [
        ("/res/cache/images", "HOST_CONTROLLER_DOCKER_DATA_DIR"),
        ("/res/cache/build-helper", "HOST_BUILD_HELPER_CACHE_DIR"),
        ("/res/cache/controller", "HOST_CONTROLLER_DOCKER_DATA_DIR"),
        ("/res/runtime", "HOST_RUNTIME_DIR"),
        ("/res", "HOST_RES_DIR"),
        ("/results", "HOST_CONTROLLER_RESULTS_DIR"),
    ]

    for container_prefix_raw, host_config_name in mount_mappings:
        host_prefix_raw = config_str(config, host_config_name, "").strip()

        if not host_prefix_raw:
            continue

        container_prefix = Path(container_prefix_raw).resolve()
        host_prefix = Path(host_prefix_raw).resolve()

        try:
            relative = source.relative_to(container_prefix)
        except ValueError:
            continue

        return (host_prefix / relative).resolve()

    code_root = Path(config_str(config, "CODE_ROOT", "/workdir/code", allow_empty=False)).resolve()
    host_code_root = Path(config_str(config, "HOST_CODE_ROOT", str(code_root))).resolve()

    try:
        relative = source.relative_to(code_root)
    except ValueError:
        return source

    return (host_code_root / relative).resolve()


def docker_container_exists(container_name: str, config: Config | None = None) -> bool:
    """Check whether a Docker container exists."""
    completed = run_cmd(
        ["docker", "container", "inspect", container_name],
        check=False,
        quiet=True,
        config=config,
    )
    return completed.returncode == 0


def docker_network_exists(network_name: str, config: Config | None = None) -> bool:
    """Check whether a Docker network exists."""
    completed = run_cmd(
        ["docker", "network", "inspect", network_name],
        check=False,
        quiet=True,
        config=config,
    )
    return completed.returncode == 0


def ensure_docker_network(network_name: str, config: Config | None = None) -> None:
    """Create a Docker network when missing."""
    if docker_network_exists(network_name, config):
        return
    command = ["docker", "network", "create"]
    if config is not None:
        lab_name = config_str(config, "LAB_NAME", config_str(config, "CLUSTER_PROFILE", ""), allow_empty=True).strip()
        if lab_name:
            command.extend(["--label", f"honeypot.lab={lab_name}"])
    command.append(network_name)
    run_cmd(command, config=config)
    log(f"Created Docker network {network_name}.")


def remove_container_if_exists(container_name: str, config: Config | None = None) -> None:
    """Remove a Docker container when present."""
    if docker_container_exists(container_name, config):
        run_cmd(["docker", "rm", "-f", container_name], check=False, quiet=True, config=config)


def docker_running_container_exists(container_name: str, config: Config | None = None) -> bool:
    """Check whether a Docker container exists and is running."""
    completed = run_cmd(
        ["docker", "container", "inspect", "-f", "{{.State.Running}}", container_name],
        check=False,
        capture_output=True,
        config=config,
    )
    return completed.returncode == 0 and completed.stdout.strip().lower() == "true"


def docker_container_networks(container_name: str, config: Config | None = None) -> list[str]:
    """Return the Docker networks attached to a container."""
    completed = run_cmd(
        ["docker", "container", "inspect", container_name],
        capture_output=True,
        config=config,
    )
    data = json.loads(completed.stdout)
    if not data:
        return []
    networks = data[0].get("NetworkSettings", {}).get("Networks", {}) or {}
    return sorted(networks.keys())


def ensure_container_network_connected(
    container_name: str,
    network_name: str,
    config: Config | None = None,
) -> None:
    """Attach a container to a Docker network when needed."""
    completed = run_cmd(
        ["docker", "inspect", "-f", "{{json .NetworkSettings.Networks}}", container_name],
        check=False,
        capture_output=True,
        config=config,
    )
    if completed.returncode != 0:
        return

    try:
        networks = json.loads(completed.stdout or "{}")
    except json.JSONDecodeError:
        networks = {}

    if network_name in networks:
        return

    run_cmd(["docker", "network", "connect", network_name, container_name], check=False, config=config)


def docker_first_container_ip(container_name: str, config: Config | None = None) -> str:
    """Return the first Docker IP address for a container, or ''."""
    completed = run_cmd(
        ["docker", "container", "inspect", container_name],
        check=False,
        capture_output=True,
        config=config,
    )

    if completed.returncode != 0 or not completed.stdout.strip():
        return ""

    try:
        data = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return ""

    if not data:
        return ""

    networks = data[0].get("NetworkSettings", {}).get("Networks", {}) or {}
    for network_data in networks.values():
        ip = network_data.get("IPAddress", "")
        if ip:
            return ip
    return ""


def docker_image_exists(
    image: str,
    config: Config | None = None,
    *,
    env: Mapping[str, str] | None = None,
) -> bool:
    """Check whether a Docker image exists locally."""
    completed = run_cmd(
        ["docker", "image", "inspect", image],
        check=False,
        quiet=True,
        config=config,
        env=env,
    )
    return completed.returncode == 0


def docker_build_image(
    image_ref: str,
    context: Path,
    *,
    dockerfile: Path | None = None,
    build_args: Mapping[str, str] | None = None,
    force: bool = False,
    timeout_seconds: int | float | None = None,
    config: Config | None = None,
    env: Mapping[str, str] | None = None,
) -> None:
    """Build a Docker image if missing or forced."""
    if not force and docker_image_exists(image_ref, config, env=env):
        log(f"Image already exists locally, skipping build: {image_ref}")
        return

    command = ["docker", "build", "-t", image_ref]
    if dockerfile is not None:
        command.extend(["-f", str(dockerfile)])
    for key, value in (build_args or {}).items():
        command.extend(["--build-arg", f"{key}={value}"])
    command.append(str(context))

    run_cmd(command, timeout_seconds=timeout_seconds, config=config, env=env)


def refresh_docker_ip_cache(config: Config | None = None) -> None:
    """Rebuild the Docker IP -> 'container;networks' cache."""
    global HONEYPOT_DOCKER_IP_CACHE, HONEYPOT_DOCKER_IP_CACHE_READY

    HONEYPOT_DOCKER_IP_CACHE = {}
    HONEYPOT_DOCKER_IP_CACHE_READY = False

    ps = run_cmd(
        ["docker", "ps", "-q"],
        check=False,
        capture_output=True,
        config=config,
    )

    cids = [line.strip() for line in ps.stdout.splitlines() if line.strip()]
    if not cids:
        HONEYPOT_DOCKER_IP_CACHE_READY = True
        return

    inspect_cmd = [
        "docker",
        "inspect",
        "-f",
        "{{.Name}}|"
        "{{range $k,$v := .NetworkSettings.Networks}}{{$k}} {{end}}|"
        "{{range $k,$v := .NetworkSettings.Networks}}{{$v.IPAddress}} {{end}}",
        *cids,
    ]

    inspected = run_cmd(
        inspect_cmd,
        check=False,
        capture_output=True,
        config=config,
    )

    for line in inspected.stdout.splitlines():
        parts = line.split("|", 2)
        if len(parts) != 3:
            continue

        name, nets, ips = parts
        name = name.removeprefix("/").strip()
        nets = " ".join(nets.split())
        ips = " ".join(ips.split())

        if not name or not ips:
            continue

        for ip in ips.split():
            if ip:
                HONEYPOT_DOCKER_IP_CACHE[ip] = f"{name};{nets}"

    HONEYPOT_DOCKER_IP_CACHE_READY = True


def find_docker_container_by_ip(ip: str, config: Config | None = None) -> str | None:
    """Find a Docker container by IP.

    Returns 'container_name;network1 network2 ...' or None.
    """
    global HONEYPOT_DOCKER_IP_CACHE_READY

    if not ip:
        return None

    if not HONEYPOT_DOCKER_IP_CACHE_READY:
        refresh_docker_ip_cache(config)

    pair = HONEYPOT_DOCKER_IP_CACHE.get(ip)
    if pair:
        return pair

    refresh_docker_ip_cache(config)
    return HONEYPOT_DOCKER_IP_CACHE.get(ip)
