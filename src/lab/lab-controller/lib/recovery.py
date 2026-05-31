#!/usr/bin/env python3
"""Smart resume validation for the lab pipeline.

A completed unit in lab-state.json only proves that it completed in a previous
process.  These checks prove that the resources produced by completed steps are
still usable after a host reboot, Docker restart, or interrupted shutdown.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, MutableMapping

from .compose import compose_environment, wait_compose_services_ready
from .config import Config, config_int
from .docker import docker_running_container_exists
from .images import cache_image_ref, load_image_definitions, registry_has_image
from .kubernetes import kind_cluster_name, kind_clusters, kind_nodes, kube_context_name, kubectl_get
from .logging import log, warn
from .state import (
    get_state_value,
    prune_completed_units_from_step,
    record_resume_check,
    state_values,
)


@dataclass(frozen=True)
class ResumeCheck:
    step_name: str
    ok: bool
    reason: str


def _path(config: Mapping[str, Any], key: str) -> Path:
    return Path(str(config.get(key, "")))


def _step_completed(state: Mapping[str, Any], step_name: str) -> bool:
    completed_steps = state.get("completed_steps", [])
    completed_units = state.get("completed_units", [])
    return (
        isinstance(completed_steps, list)
        and step_name in completed_steps
    ) or (
        isinstance(completed_units, list)
        and f"step:{step_name}" in completed_units
    )


def shutdown_cleanup_marker(config: Mapping[str, Any]) -> Path:
    generated_dir = _path(config, "GENERATED_DIR")
    return generated_dir / "shutdown-cleanup.json"


def validate_prepare(config: Config, state: Mapping[str, Any]) -> ResumeCheck:
    for key in ("RUNTIME_DIR", "GENERATED_DIR", "RESULTS_DIR", "STATE_DIR"):
        path = _path(config, key)
        if not path.is_dir():
            return ResumeCheck("00_prepare.py", False, f"{key} missing: {path}")

    state_file = _path(config, "STATE_FILE")
    if not state_file.is_file():
        return ResumeCheck("00_prepare.py", False, f"STATE_FILE missing: {state_file}")

    return ResumeCheck("00_prepare.py", True, "runtime directories and state file exist")


def validate_kind(config: Config, state: Mapping[str, Any]) -> ResumeCheck:
    cluster = kind_cluster_name(config)

    if cluster not in kind_clusters(config):
        return ResumeCheck("01_kind.py", False, f"Kind cluster missing: {cluster}")

    nodes = kind_nodes(cluster, config)
    if not nodes:
        return ResumeCheck("01_kind.py", False, f"Kind cluster has no nodes: {cluster}")

    for node in nodes:
        if not docker_running_container_exists(node, config):
            return ResumeCheck("01_kind.py", False, f"Kind node not running: {node}")

    completed = kubectl_get(
        ["get", "nodes"],
        check=False,
        capture_output=True,
        config=config,
        kube_context=kube_context_name(config),
    )
    if completed.returncode != 0:
        return ResumeCheck("01_kind.py", False, "kubectl cannot reach Kind context")

    return ResumeCheck("01_kind.py", True, "Kind cluster is reachable")


def validate_images(config: Config, state: Mapping[str, Any]) -> ResumeCheck:
    definitions = load_image_definitions(config)
    missing: list[str] = []

    for image in definitions:
        ref = cache_image_ref(config, image)
        if not registry_has_image(ref):
            missing.append(ref)

    if missing:
        sample = ", ".join(missing[:5])
        suffix = "" if len(missing) <= 5 else f" (+{len(missing) - 5} more)"
        return ResumeCheck("02_images.py", False, f"cache image(s) missing: {sample}{suffix}")

    return ResumeCheck("02_images.py", True, "cache images exist in registry")


def validate_compose(config: Config, state: Mapping[str, Any]) -> ResumeCheck:
    compose_file = Path(str(get_state_value(state, "underlay_compose_file", "")))
    if not compose_file.is_file():
        return ResumeCheck("03_compose.py", False, f"compose file missing: {compose_file}")

    services = get_state_value(state, "underlay_services", [])
    if not isinstance(services, list):
        return ResumeCheck("03_compose.py", False, "underlay_services missing from state")

    try:
        wait_compose_services_ready(
            config,
            [str(service) for service in services],
            timeout_seconds=config_int(config, "RESUME_COMPOSE_WAIT_SECONDS", 30, minimum=1),
            env=compose_environment(config),
        )
    except SystemExit as exc:
        return ResumeCheck("03_compose.py", False, f"compose services not ready: {exc}")

    return ResumeCheck("03_compose.py", True, "compose services are ready")


def _pod_name(pod: Mapping[str, Any]) -> str:
    metadata = pod.get("metadata", {})
    if not isinstance(metadata, dict):
        return "unknown/unknown"
    return f"{metadata.get('namespace', 'default')}/{metadata.get('name', 'unknown')}"


def validate_skaffold(config: Config, state: Mapping[str, Any]) -> ResumeCheck:
    skaffold_file = Path(str(get_state_value(state, "skaffold_config", "")))
    if not skaffold_file.is_file():
        return ResumeCheck("04_skaffold.py", False, f"skaffold config missing: {skaffold_file}")

    deploy_detected = bool(get_state_value(state, "skaffold_deploy_detected", False))
    if not deploy_detected:
        return ResumeCheck("04_skaffold.py", True, "no deploy section detected")

    completed = kubectl_get(
        ["get", "pods", "-A", "-o", "json"],
        check=False,
        capture_output=True,
        config=config,
        kube_context=kube_context_name(config),
    )
    if completed.returncode != 0:
        return ResumeCheck("04_skaffold.py", False, "cannot list Kubernetes pods")

    try:
        data = json.loads(completed.stdout or "{}")
    except json.JSONDecodeError:
        return ResumeCheck("04_skaffold.py", False, "kubectl returned invalid pod JSON")

    bad: list[str] = []
    ignored_namespaces = {"kube-system", "kube-public", "kube-node-lease", "local-path-storage"}

    for pod in data.get("items", []) or []:
        if not isinstance(pod, dict):
            continue
        metadata = pod.get("metadata", {}) if isinstance(pod.get("metadata", {}), dict) else {}
        namespace = str(metadata.get("namespace", "default"))
        if namespace in ignored_namespaces:
            continue

        status = pod.get("status", {}) if isinstance(pod.get("status", {}), dict) else {}
        phase = str(status.get("phase", ""))
        name = _pod_name(pod)

        if phase not in {"Running", "Succeeded"}:
            bad.append(f"{name}={phase or 'unknown'}")
            continue

        for container_status in status.get("containerStatuses", []) or []:
            if not isinstance(container_status, dict):
                continue
            if phase != "Succeeded" and not container_status.get("ready"):
                bad.append(f"{name}/{container_status.get('name', 'container')} not ready")

    if bad:
        sample = ", ".join(bad[:5])
        suffix = "" if len(bad) <= 5 else f" (+{len(bad) - 5} more)"
        return ResumeCheck("04_skaffold.py", False, f"pods not ready: {sample}{suffix}")

    return ResumeCheck("04_skaffold.py", True, "skaffold workloads are ready")


VALIDATORS = {
    "00_prepare.py": validate_prepare,
    "01_kind.py": validate_kind,
    "02_images.py": validate_images,
    "03_compose.py": validate_compose,
    "04_skaffold.py": validate_skaffold,
}


def invalidate_from_step(
    *,
    state: MutableMapping[str, Any],
    step_name: str,
    step_order: list[str],
    unit_order: list[str],
    unit_to_step: Mapping[str, str],
    reason: str,
) -> None:
    affected_units = prune_completed_units_from_step(
        state,
        step_name,
        step_order=step_order,
        unit_order=unit_order,
        unit_to_step=unit_to_step,
    )
    values = state_values(state)
    values["resume_invalidated_from_step"] = step_name
    values["resume_invalidated_reason"] = reason
    values["resume_invalidated_units"] = affected_units


def validate_resume_state(
    *,
    config: Config,
    state: MutableMapping[str, Any],
    steps: list[Path],
    unit_order: list[str],
    unit_to_step: Mapping[str, str],
) -> None:
    """Validate completed pipeline resources and prune stale completion state."""
    step_order = [step.name for step in steps]
    marker = shutdown_cleanup_marker(config)

    if marker.is_file():
        reason = f"previous shutdown cleanup marker exists: {marker}"
        first_recoverable = "01_kind.py" if "01_kind.py" in step_order else step_order[0] if step_order else ""
        if first_recoverable:
            log(f"Resume validation: {reason}; invalidating from {first_recoverable}.")
            record_resume_check(state, first_recoverable, ok=False, reason=reason)
            invalidate_from_step(
                state=state,
                step_name=first_recoverable,
                step_order=step_order,
                unit_order=unit_order,
                unit_to_step=unit_to_step,
                reason=reason,
            )
        return

    for step in steps:
        step_name = step.name
        if not _step_completed(state, step_name):
            continue

        validator = VALIDATORS.get(step_name)
        if validator is None:
            record_resume_check(state, step_name, ok=True, reason="no resume validator registered")
            continue

        try:
            check = validator(config, state)
        except Exception as exc:  # conservative: rerun from this step on probe failure
            check = ResumeCheck(step_name, False, f"resume validator failed: {exc}")
            warn(f"Resume validation for {step_name} raised an error: {exc}")

        record_resume_check(state, step_name, ok=check.ok, reason=check.reason)

        if check.ok:
            log(f"Resume validation OK for {step_name}: {check.reason}")
            continue

        log(f"Resume validation failed for {step_name}: {check.reason}; rerunning from this step.")
        invalidate_from_step(
            state=state,
            step_name=step_name,
            step_order=step_order,
            unit_order=unit_order,
            unit_to_step=unit_to_step,
            reason=check.reason,
        )
        break
