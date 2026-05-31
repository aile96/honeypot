#!/usr/bin/env python3
"""Map OpenTelemetry underlay containers after Compose starts.

The generic Compose step starts the selected underlay services. This hook then
finds their runtime addresses, updates controller host mappings, and records the
registry, proxy, attacker, Caldera, and other underlay endpoints used by later
pipeline and controller stages."""

from __future__ import annotations

import ipaddress
import json
from collections.abc import Iterator
from typing import Any

from lib import (
    config_int,
    config_str,
    configured_compose_services,
    die,
    docker_first_container_ip,
    ensure_hosts_mapping,
    get_state_value,
    log,
    run_cmd,
    set_state_value,
)


def lab_container_name(base: str) -> str:
    """Return the physical Docker container name for a lab-scoped service."""
    lab_name = config_str(CONFIG, "LAB_NAME", config_str(CONFIG, "CLUSTER_PROFILE", "honeypotlab"), allow_empty=False)
    return f"{base}-{lab_name}"


def map_registry_hostname() -> None:
    """Map the registry service alias inside the controller /etc/hosts."""
    registry_name = config_str(CONFIG, "REGISTRY_NAME", "registry").strip()
    if not registry_name:
        return

    registry_ip = docker_first_container_ip(lab_container_name(registry_name), CONFIG)
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
        "registry": lab_container_name(config_str(CONFIG, "REGISTRY_NAME", "registry")),
        "caldera": lab_container_name(config_str(CONFIG, "CALDERA_SERVER", "caldera")),
        "attacker": lab_container_name(config_str(CONFIG, "ATTACKER", "attacker")),
        "samba": lab_container_name("samba-pv"),
        "load-generator": lab_container_name("load-generator"),
    }
    service_aliases = {
        "caldera": ["caldera.dock"],
        "attacker": ["caldera.outs"],
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


def docker_network_inspect(network: str) -> dict[str, Any]:
    completed = run_cmd(
        ["docker", "network", "inspect", network],
        check=False,
        capture_output=True,
        config=CONFIG,
    )
    if completed.returncode != 0 or not completed.stdout.strip():
        die(f"docker network inspect returned nothing for network {network}. Cannot compute MetalLB IPs.")

    try:
        data = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        die(f"Could not parse Docker network inspect output for {network}: {exc}")

    if not isinstance(data, list) or not data or not isinstance(data[0], dict):
        die(f"Unexpected Docker network inspect output for {network}. Cannot compute MetalLB IPs.")
    return data[0]


def docker_network_ipv4_subnet(network_data: dict[str, Any], network: str) -> ipaddress.IPv4Network:
    ipam = network_data.get("IPAM", {})
    configs = ipam.get("Config", []) if isinstance(ipam, dict) else []
    if not isinstance(configs, list):
        configs = []

    for entry in configs:
        if not isinstance(entry, dict):
            continue
        subnet_raw = str(entry.get("Subnet", "")).strip()
        if "/" not in subnet_raw or ":" in subnet_raw:
            continue
        try:
            subnet = ipaddress.ip_network(subnet_raw, strict=False)
        except ValueError as exc:
            die(f"Could not parse Docker network IPv4 subnet {subnet_raw!r} for {network}: {exc}")
        if isinstance(subnet, ipaddress.IPv4Network):
            return subnet

    die(f"No IPv4 subnet found on network {network}. Enable IPv4 on the Docker network.")


def parse_ipv4_address(value: object) -> ipaddress.IPv4Address | None:
    raw = str(value or "").strip().split("/", 1)[0]
    if not raw:
        return None
    try:
        address = ipaddress.ip_address(raw)
    except ValueError:
        return None
    return address if isinstance(address, ipaddress.IPv4Address) else None


def docker_network_used_ipv4_addresses(network_data: dict[str, Any]) -> set[ipaddress.IPv4Address]:
    used: set[ipaddress.IPv4Address] = set()

    containers = network_data.get("Containers", {})
    if isinstance(containers, dict):
        for container in containers.values():
            if not isinstance(container, dict):
                continue
            address = parse_ipv4_address(container.get("IPv4Address"))
            if address is not None:
                used.add(address)

    ipam = network_data.get("IPAM", {})
    configs = ipam.get("Config", []) if isinstance(ipam, dict) else []
    if isinstance(configs, list):
        for entry in configs:
            if not isinstance(entry, dict):
                continue
            gateway = parse_ipv4_address(entry.get("Gateway"))
            if gateway is not None:
                used.add(gateway)

            aux_addresses = entry.get("AuxiliaryAddresses", {})
            if isinstance(aux_addresses, dict):
                for value in aux_addresses.values():
                    address = parse_ipv4_address(value)
                    if address is not None:
                        used.add(address)

    return used


def preferred_metallb_first_ip(subnet: ipaddress.IPv4Network) -> ipaddress.IPv4Address:
    octets = str(subnet.network_address).split(".")
    if len(octets) != 4:
        die(f"Unexpected IPv4 subnet base: {subnet.network_address}")
    return ipaddress.IPv4Address(".".join([*octets[:3], "200"]))


def usable_ip_bounds(subnet: ipaddress.IPv4Network) -> tuple[int, int]:
    if subnet.prefixlen >= 31:
        return int(subnet.network_address), int(subnet.broadcast_address)
    return int(subnet.network_address) + 1, int(subnet.broadcast_address) - 1


def candidate_pool_starts(
    subnet: ipaddress.IPv4Network,
    preferred: ipaddress.IPv4Address,
    pool_size: int,
) -> Iterator[ipaddress.IPv4Address]:
    first_usable, last_usable = usable_ip_bounds(subnet)
    max_start = last_usable - pool_size + 1
    if max_start < first_usable:
        die(
            f"Docker network subnet {subnet} does not have {pool_size} "
            "usable adjacent IPv4 addresses for MetalLB."
        )

    preferred_start = int(preferred)
    if preferred_start < first_usable or preferred_start > max_start:
        preferred_start = first_usable

    for start in range(preferred_start, max_start + 1):
        yield ipaddress.IPv4Address(start)
    for start in range(preferred_start - 1, first_usable - 1, -1):
        yield ipaddress.IPv4Address(start)


def choose_metallb_pool(
    subnet: ipaddress.IPv4Network,
    used: set[ipaddress.IPv4Address],
    pool_size: int,
) -> list[ipaddress.IPv4Address]:
    preferred = preferred_metallb_first_ip(subnet)
    for first in candidate_pool_starts(subnet, preferred, pool_size):
        candidate = [
            ipaddress.IPv4Address(int(first) + offset)
            for offset in range(pool_size)
        ]
        if all(address not in used for address in candidate):
            return candidate
    die(
        f"No free adjacent IPv4 range of {pool_size} addresses found on "
        f"Docker network subnet {subnet} for MetalLB."
    )


def pool_ips_value(addresses: list[ipaddress.IPv4Address]) -> str:
    if len(addresses) == 1:
        return str(addresses[0])
    return f"{addresses[0]}-{addresses[-1]}"


def configure_metallb_addresses() -> tuple[str, str, str]:
    network = config_str(CONFIG, "CP_NETWORK", "lab", allow_empty=False)
    pool_size = config_int(CONFIG, "POOL_SIZE", 2, minimum=1)
    network_data = docker_network_inspect(network)
    subnet = docker_network_ipv4_subnet(network_data, network)
    used = docker_network_used_ipv4_addresses(network_data)
    addresses = choose_metallb_pool(subnet, used, pool_size)

    frontend_proxy_ip_text = str(addresses[0])
    pool = pool_ips_value(addresses)

    CONFIG["FRONTEND_PROXY_IP"] = frontend_proxy_ip_text
    CONFIG["METALLB_POOL_IPS"] = pool
    CONFIG["POOL_SIZE"] = pool_size
    set_state_value(STATE, "FRONTEND_PROXY_IP", frontend_proxy_ip_text)
    set_state_value(STATE, "METALLB_POOL_IPS", pool)
    set_state_value(STATE, "POOL_SIZE", pool_size)
    set_state_value(STATE, "docker_network_ipv4_subnet", str(subnet))
    set_state_value(
        STATE,
        "metallb_ip_selection",
        {
            "network": network,
            "subnet": str(subnet),
            "pool": pool,
            "pool_size": pool_size,
            "frontend_proxy_ip": frontend_proxy_ip_text,
            "selected_ips": [str(address) for address in addresses],
            "used_ips": sorted(str(address) for address in used),
        },
    )
    log(
        f"Selected MetalLB IP range on Docker network {network} ({subnet}): "
        f"pool={pool}, pool_size={pool_size}, frontend-proxy={frontend_proxy_ip_text}"
    )
    return str(subnet), frontend_proxy_ip_text, pool


def main() -> None:
    map_registry_hostname()
    map_underlay_hostnames(underlay_services())
    configure_metallb_addresses()


if __name__ == "__main__":
    main()
