#!/usr/bin/env python3
"""Enumerate likely in-cluster service names through Kubernetes DNS."""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path


COMMON_SERVICES = [
    "kubernetes",
    "kube-dns",
    "coredns",
    "api",
    "apiserver",
    "etcd",
    "prometheus",
    "grafana",
    "jaeger",
    "opensearch",
    "registry",
    "kafka",
    "redis",
    "valkey-cart",
    "postgres",
    "frontend",
    "frontend-proxy",
    "image-provider",
    "smtp",
    "mongodb",
    "mysql",
    "amf",
    "ausf",
    "bsf",
    "nrf",
    "nssf",
    "pcf",
    "smf",
    "udm",
    "udr",
    "upf",
    "webui",
    "open5gs-nrf",
    "open5gs-amf",
    "open5gs-smf",
    "open5gs-upf",
]

COMMON_NAMESPACES = [
    "default",
    "kube-system",
    "kube-public",
    "app",
    "dat",
    "dmz",
    "mem",
    "pay",
    "tst",
    "monitoring",
    "observability",
    "logging",
    "free5gc",
    "open5gs",
    "5gcore",
    "oai",
    "cn",
    "upf",
    "ueransim",
]

NAMESPACE_ENV_VARS = [
    "LOG_NS",
    "APP_NAMESPACE",
    "DAT_NAMESPACE",
    "DMZ_NAMESPACE",
    "MEM_NAMESPACE",
    "PAY_NAMESPACE",
    "TEST_NAMESPACE",
    "NSPROTO",
    "NSDATA",
    "NSMEM",
]


def unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        value = value.strip()
        if value and value not in seen:
            seen.add(value)
            ordered.append(value)
    return ordered


def env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on", "enabled"}


def resolv_conf() -> tuple[str, str, list[str]]:
    dns_ip = ""
    cluster_domain = "cluster.local"
    search_namespaces: list[str] = []
    try:
        lines = Path("/etc/resolv.conf").read_text(encoding="utf-8").splitlines()
    except OSError:
        return dns_ip, cluster_domain, search_namespaces

    for line in lines:
        parts = line.split()
        if not parts:
            continue
        if parts[0] == "nameserver" and len(parts) > 1 and not dns_ip:
            dns_ip = parts[1]
        if parts[0] == "search":
            for domain in parts[1:]:
                marker = ".svc."
                if marker in domain:
                    namespace, suffix = domain.split(marker, 1)
                    if namespace:
                        search_namespaces.append(namespace.split(".")[-1])
                    if suffix:
                        cluster_domain = suffix
    return dns_ip, cluster_domain, search_namespaces


def current_namespace() -> str:
    try:
        namespace = Path("/var/run/secrets/kubernetes.io/serviceaccount/namespace").read_text(
            encoding="utf-8"
        )
        return namespace.strip()
    except OSError:
        return "default"


def run_dig(dig: str, dns_ip: str, fqdn: str, timeout_seconds: float) -> list[str]:
    cmd = [
        dig,
        "+short",
        f"+time={max(1, int(timeout_seconds))}",
        "+tries=1",
    ]
    if dns_ip:
        cmd.append(f"@{dns_ip}")
    cmd.extend([fqdn, "A"])
    try:
        completed = subprocess.run(
            cmd,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=timeout_seconds + 1,
        )
    except subprocess.TimeoutExpired:
        return []
    return [
        line.strip()
        for line in completed.stdout.splitlines()
        if line.strip() and not line.strip().endswith(".")
    ]


def main() -> int:
    dig = shutil.which("dig")
    if not dig:
        print("Missing required tool: dig")
        return 1

    data_path = Path(os.environ.get("DATA_PATH", "/tmp/KCData"))
    output = data_path / "KC1" / "dns-enum-results.csv"
    output.parent.mkdir(parents=True, exist_ok=True)

    dns_ip, cluster_domain, search_namespaces = resolv_conf()
    namespaces = unique(
        [current_namespace()]
        + search_namespaces
        + [os.environ.get(name, "") for name in NAMESPACE_ENV_VARS]
        + COMMON_NAMESPACES
    )
    services = unique(COMMON_SERVICES + os.environ.get("DNS_ENUM_EXTRA_SERVICES", "").split())

    query_timeout = float(os.environ.get("DNS_ENUM_QUERY_TIMEOUT", "1"))
    budget_seconds = float(os.environ.get("DNS_ENUM_BUDGET_SECONDS", "60"))
    deadline = time.monotonic() + budget_seconds

    records: set[tuple[str, str]] = set()
    base_name = f"kubernetes.default.svc.{cluster_domain}"
    for ip in run_dig(dig, dns_ip, base_name, query_timeout):
        records.add((base_name, ip))

    exhausted_budget = False
    for namespace in namespaces:
        for service in services:
            if time.monotonic() >= deadline:
                exhausted_budget = True
                break
            fqdn = f"{service}.{namespace}.svc.{cluster_domain}"
            for ip in run_dig(dig, dns_ip, fqdn, query_timeout):
                records.add((fqdn, ip))
        if exhausted_budget:
            break

    lines = ["name,ip"] + [f"{name},{ip}" for name, ip in sorted(records)]
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")

    if len(lines) > 1:
        print(output.read_text(encoding="utf-8"), end="")
    else:
        print("No DNS hits")
        if env_bool("DNS_ENUM_REQUIRE_HITS", True):
            print(f"DNS enumeration did not resolve any service names; see {output}")
            return 1
    if exhausted_budget:
        print(f"DNS enumeration stopped after {budget_seconds:.0f}s budget")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
