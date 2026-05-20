#!/usr/bin/env python3
"""Wrap Docker Compose operations used by target underlay stacks.

This module resolves the target compose.yaml, builds the Compose environment from
CONFIG, discovers services, builds selected images, starts selected services, and
waits for container running/health status when Docker Compose supports it or when
manual polling is required."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Mapping

from .command import run_cmd
from .config import Config, config_bool, config_str, config_to_env
from .logging import log, warn
from .registry import image_version, registry_endpoint
from .target import config_list, target_conf_file


def compose_file_path(config: Mapping[str, Any]) -> Path:
    """Return the target-managed Compose file path."""
    explicit = config_str(config, "COMPOSE_FILE", "", allow_empty=True).strip()
    if explicit:
        return Path(explicit)
    return target_conf_file(config, "compose.yaml")


def compose_project_name(config: Mapping[str, Any]) -> str:
    """Return the Compose project name for the underlay stack."""
    lab_name = config_str(config, "LAB_NAME", config_str(config, "CLUSTER_PROFILE", "honeypotlab"), allow_empty=False)
    return config_str(config, "COMPOSE_PROJECT_NAME", f"honeypot-{lab_name}", allow_empty=False)


def compose_environment(config: Mapping[str, Any]) -> dict[str, str]:
    """Build the environment exposed to Docker Compose."""
    env = config_to_env(config)
    env["IMAGE_VERSION"] = image_version(config)
    env.setdefault("COMPOSE_PORT_BIND_ADDR", "0.0.0.0")
    env.setdefault("COMPOSE_PARALLEL_LIMIT", config_str(config, "DOCKER_BUILD_PARALLELISM", "4"))

    return env


def docker_compose_command(config: Config | None = None) -> list[str]:
    """Return the available Docker Compose command."""
    if run_cmd(["docker", "compose", "version"], check=False, quiet=True, config=config).returncode == 0:
        return ["docker", "compose"]
    if run_cmd(["docker-compose", "version"], check=False, quiet=True, config=config).returncode == 0:
        return ["docker-compose"]
    raise SystemExit("Docker Compose is not available as `docker compose` or `docker-compose`.")


def docker_compose_supports_wait(compose_cmd: list[str], config: Config | None = None) -> bool:
    """Return True when Compose supports `up --wait`."""
    completed = run_cmd(
        [*compose_cmd, "up", "--help"],
        check=False,
        capture_output=True,
        config=config,
    )
    return completed.returncode == 0 and "--wait" in f"{completed.stdout}\n{completed.stderr}"


def configured_compose_services(config: Mapping[str, Any], name: str, default: list[str] | None = None) -> list[str]:
    """Return a configured Compose service list."""
    return config_list(config, name, default or [])


def discover_compose_services(
    config: Config,
    *,
    env: Mapping[str, str] | None = None,
) -> list[str]:
    """Ask Docker Compose for the services in the rendered config."""
    compose_cmd = docker_compose_command(config)
    completed = run_cmd(
        [
            *compose_cmd,
            "-f",
            str(compose_file_path(config)),
            "-p",
            compose_project_name(config),
            "config",
            "--services",
        ],
        capture_output=True,
        config=config,
        env=env or compose_environment(config),
    )
    return [line.strip() for line in completed.stdout.splitlines() if line.strip()]


def compose_container_state(container_id: str, config: Config) -> dict[str, Any]:
    """Return Docker State for a Compose container, or an empty dict."""
    completed = run_cmd(
        ["docker", "container", "inspect", "-f", "{{json .State}}", container_id],
        check=False,
        capture_output=True,
        config=config,
    )
    if completed.returncode != 0 or not completed.stdout.strip():
        return {}
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError:
        return {}


def wait_compose_services_ready(
    config: Config,
    services: list[str],
    *,
    timeout_seconds: int,
    env: Mapping[str, str],
) -> None:
    """Wait until Compose service containers are running and healthy when possible."""
    if not services:
        return

    compose_cmd = docker_compose_command(config)
    deadline = time.monotonic() + timeout_seconds
    pending = set(services)
    last_status: dict[str, str] = {}

    while pending and time.monotonic() < deadline:
        for service in list(pending):
            completed = run_cmd(
                [
                    *compose_cmd,
                    "-f",
                    str(compose_file_path(config)),
                    "-p",
                    compose_project_name(config),
                    "ps",
                    "-q",
                    service,
                ],
                check=False,
                capture_output=True,
                config=config,
                env=env,
            )
            ids = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
            if not ids:
                last_status[service] = "missing"
                continue

            ready = True
            for container_id in ids:
                state = compose_container_state(container_id, config)
                if not state.get("Running"):
                    ready = False
                    last_status[service] = state.get("Status", "not running")
                    break
                health = state.get("Health")
                if health and health.get("Status") != "healthy":
                    ready = False
                    last_status[service] = health.get("Status", "unknown")
                    break

            if ready:
                pending.remove(service)

        if pending:
            time.sleep(2)

    if pending:
        details = ", ".join(f"{service}={last_status.get(service, 'unknown')}" for service in sorted(pending))
        raise SystemExit(f"Compose services did not become ready before timeout: {details}")


def compose_up(
    config: Config,
    services: list[str],
    *,
    wait_timeout_seconds: int,
    env: Mapping[str, str],
) -> None:
    """Start selected Compose services."""
    if not services:
        log("No Compose services selected for startup.")
        return

    compose_cmd = docker_compose_command(config)
    command = [
        *compose_cmd,
        "-f",
        str(compose_file_path(config)),
        "-p",
        compose_project_name(config),
        "up",
        "-d",
        "--remove-orphans",
    ]
    supports_wait = docker_compose_supports_wait(compose_cmd, config)
    if supports_wait:
        command.extend(["--wait", "--wait-timeout", str(wait_timeout_seconds)])
        command.extend(services)
        run_cmd(command, config=config, env=env)
        return

    for service in services:
        run_cmd(
            [*command, service],
            timeout_seconds=wait_timeout_seconds,
            config=config,
            env=env,
        )
    wait_compose_services_ready(config, services, timeout_seconds=wait_timeout_seconds, env=env)


def compose_down(
    config: Config,
    *,
    env: Mapping[str, str] | None = None,
) -> None:
    """Stop and remove the target Compose stack."""
    compose_file = compose_file_path(config)
    if not compose_file.is_file():
        warn(f"Compose file not found during cleanup: {compose_file}")
        return

    compose_cmd = docker_compose_command(config)
    run_cmd(
        [
            *compose_cmd,
            "-f",
            str(compose_file),
            "-p",
            compose_project_name(config),
            "down",
            "--remove-orphans",
        ],
        check=False,
        config=config,
        env=env or compose_environment(config),
    )
