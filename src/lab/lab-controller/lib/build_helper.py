#!/usr/bin/env python3
"""Manage the temporary Docker build-helper used by Skaffold builds.

The build-helper is a Docker-in-Docker container that gives Skaffold an isolated
Docker daemon while still sharing the project source, cache directories, and
registry trust material. These helpers start it, wait for readiness, expose the
right DOCKER_HOST environment, and remove it after deployment."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Mapping

from .command import run_cmd
from .config import Config, config_bool, config_int, config_str, config_to_env
from .docker import docker_bind_source, remove_container_if_exists
from .logging import die, log
from .registry import registry_endpoint


def docker_host_env(config: Config, docker_host: str) -> dict[str, str]:
    """Return a CONFIG-backed environment pointing Docker at docker_host."""
    env = config_to_env(config)
    env["DOCKER_HOST"] = docker_host
    env["DOCKER_TLS_VERIFY"] = ""
    env["DOCKER_CERT_PATH"] = ""
    return env


def wait_docker_host_ready(
    docker_env: Mapping[str, str],
    *,
    timeout_seconds: int,
) -> None:
    """Wait until a Docker daemon targeted by docker_env is ready."""
    deadline = time.monotonic() + timeout_seconds
    last_error = ""

    while time.monotonic() < deadline:
        completed = run_cmd(
            ["docker", "info"],
            check=False,
            capture_output=True,
            env=docker_env,
        )
        if completed.returncode == 0:
            return
        last_error = (completed.stderr or completed.stdout or "").strip()
        time.sleep(1)

    die(f"Build helper Docker daemon did not become ready: {last_error or 'no output'}")


def start_cluster_build_helper(config: Config) -> dict[str, str]:
    """Start a throwaway Docker-in-Docker helper with persistent layer cache."""
    name = config_str(config, "BUILD_HELPER_NAME", "cluster-build-helper")
    image = config_str(config, "BUILD_HELPER_IMAGE", "docker:29-dind")
    lab_name = config_str(config, "LAB_NAME", config_str(config, "CLUSTER_PROFILE", "honeypotlab"), allow_empty=False)
    network = config_str(config, "CP_NETWORK", f"kind-{lab_name}", allow_empty=False)
    port = config_int(config, "BUILD_HELPER_PORT", 23750, minimum=1, maximum=65535)
    cache_dir = Path(config_str(config, "BUILD_HELPER_CACHE_DIR", "/res/cache/build-helper"))
    registry = registry_endpoint(config)
    registry_scheme = config_str(config, "REGISTRY_SCHEME", "https", allow_empty=False).strip().lower()
    registry_ca_file = Path(config_str(config, "REGISTRY_CA_FILE", "/res/runtime/registry/certs/rootca.crt"))
    timeout = config_int(config, "BUILD_HELPER_READY_TIMEOUT_SECONDS", 120, minimum=1)
    host_socket = config_bool(config, "HOST_SOCKET", False)

    remove_container_if_exists(name, config)
    cache_source = docker_bind_source(cache_dir, config)
    cache_source.mkdir(parents=True, exist_ok=True)

    command = [
        "docker",
        "run",
        "-d",
        "--privileged",
        "--restart",
        "no",
        "--name",
        name,
        "--label",
        f"honeypot.lab={lab_name}",
        "--label",
        "honeypot.role=build-helper",
        "--network",
        network,
        "-e",
        "DOCKER_TLS_CERTDIR=",
        "-v",
        f"{cache_source}:/var/lib/docker",
    ]

    helper_host = name if host_socket else "127.0.0.1"
    if not host_socket:
        command.extend(["-p", f"127.0.0.1:{port}:2375"])

    if registry_scheme == "https" and registry_ca_file.is_file():
        ca_source = docker_bind_source(registry_ca_file, config)
        command.extend(
            [
                "--mount",
                f"type=bind,source={ca_source},target=/etc/docker/certs.d/{registry}/ca.crt,readonly",
            ]
        )

    daemon_args = [
        image,
        "--host=tcp://0.0.0.0:2375",
        "--host=unix:///var/run/docker.sock",
    ]

    if registry_scheme != "https":
        daemon_args.append(f"--insecure-registry={registry}")

    command.extend(daemon_args)

    run_cmd(
        command,
        config=config,
    )

    helper_env = docker_host_env(config, f"tcp://{helper_host}:2375" if host_socket else f"tcp://{helper_host}:{port}")
    wait_docker_host_ready(helper_env, timeout_seconds=timeout)
    log(f"Started cluster build helper {name} with cache {cache_dir}.")
    return helper_env


def stop_cluster_build_helper(config: Config) -> None:
    """Remove the cluster build helper if it exists."""
    name = config_str(config, "BUILD_HELPER_NAME", "cluster-build-helper")
    remove_container_if_exists(name, config)
    log(f"Removed cluster build helper {name}.")
