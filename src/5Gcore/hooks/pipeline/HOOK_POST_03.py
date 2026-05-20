#!/usr/bin/env python3
"""Map 5Gcore underlay containers after Compose starts."""

from lib import (
    config_str,
    configured_compose_services,
    docker_first_container_ip,
    ensure_hosts_mapping,
    get_state_value,
    log,
    set_state_value,
)


def lab_container_name(base: str) -> str:
    """Return the physical Docker container name for a lab-scoped service."""
    lab_name = config_str(CONFIG, "LAB_NAME", config_str(CONFIG, "CLUSTER_PROFILE", "honeypotlab"), allow_empty=False)
    return f"{base}-{lab_name}"


def underlay_services() -> list[str]:
    """Return Compose services that were started by the 02 pipeline step."""
    services = get_state_value(STATE, "underlay_services", [])
    if isinstance(services, list):
        return [str(service) for service in services if str(service).strip()]
    configured = configured_compose_services(CONFIG, "COMPOSE_DEPLOY_SERVICES", [])
    if configured:
        return configured
    return configured_compose_services(CONFIG, "COMPOSE_SERVICES", [])


def map_underlay_hostnames(services: list[str]) -> None:
    """Map well-known underlay hostnames in the controller /etc/hosts."""
    service_containers = {
        "registry": lab_container_name(config_str(CONFIG, "REGISTRY_NAME", "registry")),
        "caldera": lab_container_name(config_str(CONFIG, "CALDERA_SERVER", "caldera")),
        "attacker": lab_container_name(config_str(CONFIG, "ATTACKER", "attacker")),
        "samba": lab_container_name("samba-pv"),
    }
    service_aliases = {
        "caldera": ["caldera.dock"],
        "attacker": ["caldera.outs"],
        "samba": ["samba-pv"],
    }
    mappings: dict[str, str] = {}
    for service in dict.fromkeys(services):
        container = service_containers.get(service, service)
        ip = docker_first_container_ip(container, CONFIG)
        if not ip:
            log(f"Underlay container {container!r} has no Docker IP yet; skipping /etc/hosts mapping.")
            continue
        aliases = [service, *service_aliases.get(service, [])]
        if service == "registry":
            aliases.append(config_str(CONFIG, "REGISTRY_NAME", "registry"))
        if service == "caldera":
            aliases.append(config_str(CONFIG, "CALDERA_SERVER", "caldera"))
        if service == "attacker":
            aliases.append(config_str(CONFIG, "ATTACKER", "attacker"))
        for hostname in dict.fromkeys(aliases):
            ensure_hosts_mapping(hostname, ip, require_loopback_if_existing=False)
        mappings[service] = ip
    set_state_value(STATE, "underlay_hosts_mapping", mappings)


def main() -> None:
    map_underlay_hostnames(underlay_services())
    log("5Gcore underlay host mappings recorded.")


if __name__ == "__main__":
    main()
