#!/usr/bin/env python3
"""Map OpenTelemetry underlay containers after Compose starts.

The generic Compose step starts the selected underlay services. This hook then
finds their runtime addresses, updates controller host mappings, and records the
registry, proxy, attacker, Caldera, and other underlay endpoints used by later
pipeline and controller stages."""

from lib import (
    config_str,
    configured_compose_services,
    docker_first_container_ip,
    ensure_hosts_mapping,
    get_state_value,
    log,
    set_state_value,
)


def map_registry_hostname() -> None:
    """Map the registry container name inside the controller /etc/hosts."""
    registry_name = config_str(CONFIG, "REGISTRY_NAME", "registry").strip()
    if not registry_name:
        return

    registry_ip = docker_first_container_ip(registry_name, CONFIG)
    if not registry_ip:
        log(f"Registry container {registry_name!r} has no Docker IP yet; skipping /etc/hosts mapping.")
        return

    ensure_hosts_mapping(
        registry_name,
        registry_ip,
        require_loopback_if_existing=False,
    )
    set_state_value(STATE, "registry_hosts_mapping", {registry_name: registry_ip})


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
    mappings: dict[str, str] = {}
    service_containers = {
        "registry": config_str(CONFIG, "REGISTRY_NAME", "registry"),
        "caldera": config_str(CONFIG, "CALDERA_SERVER", "caldera"),
        "attacker": config_str(CONFIG, "ATTACKER", "attacker"),
        "router": config_str(CONFIG, "PROXY", "router"),
        "samba": "samba-pv",
        "load-generator": "load-generator",
    }
    service_aliases = {
        "caldera": ["caldera.dock"],
        "attacker": ["caldera.outs"],
        "router": ["proxy"],
        "samba": ["samba-pv"],
    }

    for service in dict.fromkeys(services):
        container = service_containers.get(service, service)
        ip = docker_first_container_ip(container, CONFIG)
        if not ip:
            log(f"Underlay container {container!r} has no Docker IP yet; skipping /etc/hosts mapping.")
            continue

        for hostname in dict.fromkeys([service, container, *service_aliases.get(service, [])]):
            ensure_hosts_mapping(
                hostname,
                ip,
                require_loopback_if_existing=False,
            )

        mappings[service] = ip

    if mappings:
        set_state_value(STATE, "underlay_hosts_mapping", mappings)


def main() -> None:
    map_registry_hostname()
    map_underlay_hostnames(underlay_services())


if __name__ == "__main__":
    main()