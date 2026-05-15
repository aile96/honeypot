#!/usr/bin/env python3
"""Clean up target underlay containers, helper containers, and Kind clusters.

These helpers are used by shutdown and recovery paths. They discover configured
Compose services, remove known helper containers, tear down Compose stacks, and
delete Kind clusters without requiring each target to duplicate cleanup logic."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any, Mapping

from .compose import compose_down
from .config import Config, config_to_env
from .docker_runtime import docker_env, to_text
from .logging import log, warn
from .target import config_list

DEFAULT_COMPOSE_CONTAINER_NAMES = ["registry", "caldera", "attacker", "samba", "load-generator", "router"]


def discover_underlay_container_names(config: Mapping[str, Any] | None = None) -> list[str]:
    """Discover Compose container names from CONFIG."""
    config = config or {}
    names: set[str] = set()

    for key in ("COMPOSE_DEPLOY_SERVICES", "COMPOSE_SERVICES", "COMPOSE_BUILD_SERVICES", "COMPOSE_BOOTSTRAP_SERVICES"):
        names.update(config_list(config, key, []))

    for key, default in (
        ("REGISTRY_NAME", "registry"),
        ("CALDERA_SERVER", "caldera"),
        ("ATTACKER", "attacker"),
        ("PROXY", "router"),
    ):
        value = str(config.get(key, default)).strip()
        if value:
            names.add(value)

    if not names:
        names.update(DEFAULT_COMPOSE_CONTAINER_NAMES)

    return sorted(names)


def docker_container_exists_for_cleanup(container_name: str, env: dict[str, str]) -> bool:
    """Check whether a container exists without raising."""
    try:
        completed = subprocess.run(
            ["docker", "container", "inspect", container_name],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False

    return completed.returncode == 0


def cleanup_underlay_containers(socket_path: Path, config: Config | None = None) -> None:
    """Remove target Compose containers discovered from configuration."""
    container_names = discover_underlay_container_names(config)

    if not container_names:
        log("No Compose container names found for Docker cleanup.")
        return

    env = docker_env(socket_path)
    removed: list[str] = []

    for container_name in container_names:
        if not docker_container_exists_for_cleanup(container_name, env):
            continue

        warn(f"Removing Compose container '{container_name}'.")

        try:
            completed = subprocess.run(
                ["docker", "rm", "-f", container_name],
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=30,
                check=False,
            )
        except FileNotFoundError:
            warn("docker CLI not found while cleaning up Compose containers.")
            return
        except subprocess.TimeoutExpired:
            warn(f"Timed out removing Compose container '{container_name}'.")
            continue
        except OSError as exc:
            warn(f"Could not remove Compose container '{container_name}': {exc}")
            continue

        if completed.returncode == 0:
            removed.append(container_name)
        else:
            stderr = to_text(completed.stderr).strip()
            warn(
                f"Failed to remove Compose container '{container_name}': "
                f"{stderr or f'docker rm exited with {completed.returncode}'}"
            )

    if removed:
        log(f"Removed Compose containers: {', '.join(removed)}")
    else:
        log("No running or stopped Compose containers needed cleanup.")


def cleanup_docker_compose_stack(socket_path: Path, config: Config | None = None) -> None:
    """Stop and remove the target Docker Compose stack."""
    if not config:
        log("No CONFIG loaded; skipping Docker Compose cleanup.")
        return

    env = config_to_env(config, base_env=docker_env(socket_path))
    compose_down(config, env=env)


def infer_kind_cluster_name(config: Mapping[str, Any] | None = None, *, env: dict[str, str] | None = None) -> str:
    """Infer the Kind cluster name from CONFIG or local kind state."""
    config = config or {}
    kube_ctx = str(config.get("KUBE_CONTEXT", "")).strip()
    cluster_profile = str(config.get("CLUSTER_PROFILE", "")).strip()

    if cluster_profile:
        return cluster_profile

    if kube_ctx.startswith("kind-") and len(kube_ctx) > len("kind-"):
        return kube_ctx.removeprefix("kind-")

    try:
        completed = subprocess.run(
            ["kind", "get", "clusters"],
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=10,
            check=False,
        )
    except FileNotFoundError:
        log("kind CLI not found; skipping Kind cluster cleanup.")
        return ""
    except subprocess.TimeoutExpired:
        warn("Timed out while querying Kind clusters; skipping cleanup.")
        return ""

    clusters = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    if len(clusters) == 1:
        return clusters[0]

    log(
        "Unable to infer Kind cluster to delete "
        f"(found: {clusters}). Set CLUSTER_PROFILE to enable cleanup."
    )
    return ""


def cleanup_kind_cluster(config: Config | None = None, socket_path: Path | None = None) -> None:
    """Attempt to delete the local Kind cluster used by the lab."""
    env = docker_env(socket_path) if socket_path is not None else None
    cluster_name = infer_kind_cluster_name(config, env=env)
    if not cluster_name:
        return

    try:
        completed = subprocess.run(
            ["kind", "get", "clusters"],
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=10,
            check=False,
        )
    except FileNotFoundError:
        log("kind CLI not found; skipping Kind cluster cleanup.")
        return
    except subprocess.TimeoutExpired:
        warn("Timed out while querying Kind clusters; skipping cleanup.")
        return

    available = {line.strip() for line in completed.stdout.splitlines() if line.strip()}

    if cluster_name not in available:
        log(f"Kind cluster '{cluster_name}' not present locally; nothing to delete.")
        return

    warn(f"Deleting Kind cluster '{cluster_name}'...")

    try:
        completed = subprocess.run(
            ["kind", "delete", "cluster", "--name", cluster_name],
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=300,
            check=False,
        )
    except FileNotFoundError:
        log("kind CLI not found while deleting cluster; skipping.")
        return
    except subprocess.TimeoutExpired:
        warn(f"Timed out deleting Kind cluster '{cluster_name}'.")
        return

    if completed.returncode == 0:
        log(f"Kind cluster '{cluster_name}' deleted successfully.")
    else:
        stderr = to_text(completed.stderr).strip()
        warn(
            f"Failed to delete Kind cluster '{cluster_name}': "
            f"{stderr or f'kind delete exited with {completed.returncode}'}"
        )
