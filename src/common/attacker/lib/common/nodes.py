#!/usr/bin/env python3
"""Shared node parsing helpers for attacker scripts."""

from __future__ import annotations

from pathlib import Path


def worker_node_ips(iphost_file: str | Path = "/tmp/iphost") -> list[str]:
    """Read 'IP - HOST' entries and return unique worker node IPv4 addresses."""
    path = Path(iphost_file)
    if not path.is_file():
        raise FileNotFoundError(str(path))

    ips: set[str] = set()
    for raw_line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "-" not in line:
            continue
        ip, host = [part.strip() for part in line.split("-", 1)]
        if host.startswith("worker") and ip.count(".") == 3:
            ips.add(ip)
    return sorted(ips)
