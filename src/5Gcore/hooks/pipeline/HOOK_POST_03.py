#!/usr/bin/env python3
"""Finalize the 5Gcore deployment after Skaffold completes."""

from __future__ import annotations

import json
import re
from typing import Any

from lib import (
    config_bool,
    config_str,
    die,
    docker_container_exists,
    get_state_value,
    kubectl,
    log,
    run_cmd,
    set_state_value,
    stop_cluster_build_helper,
    warn,
)


def lab_container_name(base: str) -> str:
    lab_name = config_str(CONFIG, "LAB_NAME", config_str(CONFIG, "CLUSTER_PROFILE", "honeypotlab"), allow_empty=False)
    suffix = f"-{lab_name}"
    if base.endswith(suffix):
        return base
    return f"{base}{suffix}"


def coredns_configmap() -> dict[str, Any]:
    completed = kubectl(
        CONFIG,
        ["-n", "kube-system", "get", "configmap", "coredns", "-o", "json"],
        capture_output=True,
    )
    return json.loads(completed.stdout)


def apply_coredns_configmap(configmap: dict[str, Any]) -> None:
    kubectl(
        CONFIG,
        ["-n", "kube-system", "apply", "-f", "-"],
        input_text=json.dumps(configmap),
        quiet=True,
    )


def restart_coredns() -> None:
    kubectl(CONFIG, ["-n", "kube-system", "rollout", "restart", "deployment", "coredns"])


def remove_forward_directives(corefile: str) -> tuple[str, bool]:
    output: list[str] = []
    changed = False
    skipping = False
    depth = 0

    for line in corefile.splitlines():
        stripped = line.lstrip()

        if skipping:
            depth += line.count("{") - line.count("}")
            changed = True
            if depth <= 0:
                skipping = False
            continue

        if re.match(r"^forward\s+", stripped):
            changed = True
            if "{" in line:
                depth = line.count("{") - line.count("}")
                skipping = depth > 0
            continue

        output.append(line)

    return "\n".join(output) + "\n", changed


def remove_zone_block(corefile: str, zone: str) -> str:
    output: list[str] = []
    skipping = False
    depth = 0
    pattern = re.compile(rf"^{re.escape(zone)}(:[0-9]+)?\s*\{{")

    for line in corefile.splitlines():
        stripped = line.lstrip()

        if skipping:
            depth += line.count("{") - line.count("}")
            if depth <= 0:
                skipping = False
            continue

        if pattern.match(stripped):
            skipping = True
            depth = line.count("{") - line.count("}")
            if depth <= 0:
                skipping = False
            continue

        output.append(line)

    return "\n".join(output) + "\n"


def zone_block(zone: str, docker_ip: str) -> str:
    return "\n".join(
        [
            f"{zone}:53 {{",
            "    errors",
            "    cache 30",
            f"    forward . {docker_ip}",
            "    reload",
            "}",
        ]
    )


def insert_zone_block(corefile: str, zone: str, docker_ip: str) -> tuple[str, bool]:
    if re.search(rf"(?m)^\s*{re.escape(zone)}(:[0-9]+)?\s*\{{", corefile):
        log(f"Zone block for {zone!r} already present in Corefile. Nothing to do.")
        return corefile, False

    cleaned = remove_zone_block(corefile, zone)
    block = zone_block(zone, docker_ip)
    lines = cleaned.splitlines()
    output: list[str] = []
    inserted = False
    main_server = re.compile(r"^\s*(?:\.)?:53\s*\{")

    for line in lines:
        if not inserted and main_server.match(line):
            output.extend(block.splitlines())
            output.append("")
            inserted = True

        output.append(line)

    if not inserted:
        output = [*block.splitlines(), "", *output]

    return "\n".join(output) + "\n", True


def docker_container_ip(container_name: str) -> str:
    completed = run_cmd(
        ["docker", "inspect", "-f", "{{json .NetworkSettings.Networks}}", container_name],
        check=False,
        capture_output=True,
        config=CONFIG,
    )

    if completed.returncode != 0 or not completed.stdout.strip():
        return ""

    try:
        networks = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return ""

    preferred_network = config_str(CONFIG, "CP_NETWORK", "", allow_empty=True).strip()
    if preferred_network and preferred_network in networks:
        ip = str(networks[preferred_network].get("IPAddress", "")).strip()
        if ip:
            return ip

    for network_data in networks.values():
        ip = str(network_data.get("IPAddress", "")).strip()
        if ip:
            return ip

    return ""


def configure_coredns() -> None:
    """Apply the same RECURSIVE_DNS/CoreDNS behavior used by opentelemetry."""
    configmap = coredns_configmap()
    data = configmap.setdefault("data", {})
    corefile = str(data.get("Corefile", ""))

    if not corefile:
        die("Failed to read kube-system/coredns Corefile.")

    if not config_bool(CONFIG, "RECURSIVE_DNS", True):
        new_corefile, changed = remove_forward_directives(corefile)
        if changed:
            data["Corefile"] = new_corefile
            apply_coredns_configmap(configmap)
            restart_coredns()
            log("CoreDNS recursion disabled by removing forward directives.")
        else:
            log("No CoreDNS forward directive found; nothing to change.")

        set_state_value(STATE, "coredns_recursive_dns", False)
        return

    attacker = config_str(CONFIG, "ATTACKER", "attacker", allow_empty=False)
    attacker_container = lab_container_name(attacker)

    if not docker_container_exists(attacker_container, CONFIG):
        warn(f"Container {attacker_container!r} not found. Skipping attacker DNS zone insertion.")
        set_state_value(STATE, "coredns_attacker_zone_inserted", False)
        return

    docker_ip = docker_container_ip(attacker_container)
    if not docker_ip:
        die(f"Could not determine IP for docker container {attacker_container!r}.")

    new_corefile, changed = insert_zone_block(corefile, attacker, docker_ip)

    if changed:
        data["Corefile"] = new_corefile
        apply_coredns_configmap(configmap)
        restart_coredns()
        log(f"Inserted CoreDNS zone block for {attacker!r} forwarding to {docker_ip}.")

    set_state_value(
        STATE,
        "coredns_attacker_zone",
        {
            "zone": attacker,
            "container": attacker_container,
            "docker_ip": docker_ip,
            "inserted_or_present": True,
        },
    )


def stop_skaffold_build_helper_if_started() -> None:
    if get_state_value(STATE, "skaffold_build_helper_started", False):
        stop_cluster_build_helper(CONFIG)
        set_state_value(STATE, "skaffold_build_helper_stopped", True)


def main() -> None:
    stop_skaffold_build_helper_if_started()
    configure_coredns()
    log("5Gcore Skaffold finalization completed.")


if __name__ == "__main__":
    main()
