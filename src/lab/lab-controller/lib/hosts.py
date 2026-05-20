#!/usr/bin/env python3
"""Manage temporary hostname mappings inside the controller container.

Some underlay services must be reachable by stable names while the lab runs. This
module adds or updates /etc/hosts entries in a controlled way so hooks can map
container names to discovered addresses without duplicating file-editing logic."""

from __future__ import annotations

import ipaddress
from pathlib import Path

from .logging import die, log


def is_loopback_ip(value: str) -> bool:
    """Return True if value is a loopback IPv4 or IPv6 address."""
    try:
        return ipaddress.ip_address(value).is_loopback
    except ValueError:
        return False


def parse_hosts_entry(line: str) -> tuple[str, list[str]] | None:
    """Parse one /etc/hosts line into (ip, hostnames)."""
    clean = line.split("#", 1)[0].strip()
    if not clean:
        return None

    parts = clean.split()
    if len(parts) < 2:
        return None

    return parts[0], parts[1:]


def ensure_hosts_mapping(
    hostname: str,
    ip: str,
    *,
    hosts_file: str | Path = "/etc/hosts",
    require_loopback_if_existing: bool = True,
) -> None:
    """Ensure hosts_file maps hostname to ip.

    If hostname already exists with a non-loopback mapping and
    require_loopback_if_existing=True, fail instead of silently overriding it.
    """
    path = Path(hosts_file)

    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        die(f"{path} does not exist.")
    except OSError as exc:
        die(f"Could not read {path}: {exc}")

    found_desired = False
    conflicts: list[str] = []

    for line in lines:
        parsed = parse_hosts_entry(line)
        if parsed is None:
            continue

        ip_value, hostnames = parsed
        if hostname not in hostnames:
            continue

        if ip_value == ip:
            found_desired = True
            continue

        if require_loopback_if_existing and not is_loopback_ip(ip_value):
            conflicts.append(f"{hostname} -> {ip_value}")

    if conflicts:
        die(f"{path} already contains a non-local mapping for {hostname}: {', '.join(conflicts)}")

    if found_desired:
        log(f"Verified {path} already maps {hostname} to {ip}.")
        return

    try:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(f"\n# Added by container entrypoint\n{ip}\t{hostname}\n")
    except OSError as exc:
        die(f"Could not update {path}: {exc}")

    log(f"Added {hostname} -> {ip} to {path}.")
