#!/usr/bin/env python3
"""Provide Kubernetes and Kind helper functions.

The functions in this module build kubectl and kind commands from CONFIG, derive
cluster and context names, discover Kind nodes, and collect information needed by
pipeline steps and target hooks without hard-coding those details everywhere."""

from __future__ import annotations

import json
import subprocess

from .command import run_cmd
from .config import Config, config_str


def kind_cluster_name(config: Config) -> str:
    """Return the Kind cluster name for the lab."""
    return config_str(config, "LAB_NAME", config_str(config, "CLUSTER_PROFILE", "honeypotlab"), allow_empty=False)


def kube_context_name(config: Config) -> str:
    """Return the Kubernetes context used by the lab."""
    explicit = config_str(config, "KUBE_CONTEXT", "").strip()
    return explicit or f"kind-{kind_cluster_name(config)}"


def kubectl_ctx(
    kube_context: str,
    args: list[str],
    *,
    check: bool = True,
    capture_output: bool = False,
    input_text: str | None = None,
    quiet: bool = False,
    raise_on_error: bool = False,
    config: Config | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run kubectl with an explicit context."""
    return run_cmd(
        ["kubectl", "--context", kube_context, *args],
        check=check,
        capture_output=capture_output,
        input_text=input_text,
        quiet=quiet,
        raise_on_error=raise_on_error,
        config=config,
    )


def kubectl(
    config: Config,
    args: list[str],
    *,
    check: bool = True,
    capture_output: bool = False,
    input_text: str | None = None,
    quiet: bool = False,
    raise_on_error: bool = False,
) -> subprocess.CompletedProcess[str]:
    """Run kubectl against the lab context."""
    return kubectl_ctx(
        kube_context_name(config),
        args,
        check=check,
        capture_output=capture_output,
        input_text=input_text,
        quiet=quiet,
        raise_on_error=raise_on_error,
        config=config,
    )


def kubectl_get(
    args: list[str],
    *,
    check: bool = True,
    capture_output: bool = False,
    input_text: str | None = None,
    quiet: bool = False,
    raise_on_error: bool = False,
    config: Config | None = None,
    kube_context: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run kubectl using kube_context or CONFIG['KUBE_CONTEXT']."""
    context = kube_context
    if context is None and config is not None:
        context = config_str(config, "KUBE_CONTEXT", "").strip()

    cmd = ["kubectl", "--context", context, *args] if context else ["kubectl", *args]

    return run_cmd(
        cmd,
        check=check,
        capture_output=capture_output,
        input_text=input_text,
        quiet=quiet,
        raise_on_error=raise_on_error,
        config=config,
    )


def kubectl_get_or_raise(
    args: list[str],
    *,
    check: bool = True,
    capture_output: bool = False,
    input_text: str | None = None,
    quiet: bool = False,
    config: Config | None = None,
    kube_context: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run kubectl and raise CommandError on failure."""
    return kubectl_get(
        args,
        check=check,
        capture_output=capture_output,
        input_text=input_text,
        quiet=quiet,
        raise_on_error=True,
        config=config,
        kube_context=kube_context,
    )


def kind_cmd(
    args: list[str],
    *,
    check: bool = True,
    capture_output: bool = False,
    config: Config | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run kind."""
    return run_cmd(
        ["kind", *args],
        check=check,
        capture_output=capture_output,
        config=config,
    )


def kind_clusters(config: Config | None = None) -> list[str]:
    """Return local KinD cluster names."""
    completed = kind_cmd(["get", "clusters"], check=False, capture_output=True, config=config)
    return [line.strip() for line in completed.stdout.splitlines() if line.strip()]


def kind_nodes(cluster_name: str, config: Config | None = None) -> list[str]:
    """Return node container names for a KinD cluster."""
    completed = kind_cmd(
        ["get", "nodes", "--name", cluster_name],
        check=False,
        capture_output=True,
        config=config,
    )
    if completed.returncode != 0:
        return []
    return [line.strip() for line in completed.stdout.splitlines() if line.strip()]


def get_current_kube_context(
    *,
    raise_on_error: bool = False,
    config: Config | None = None,
) -> str:
    """Return the current Kubernetes context, or ''."""
    completed = run_cmd(
        ["kubectl", "config", "current-context"],
        check=False,
        capture_output=True,
        raise_on_error=raise_on_error,
        config=config,
    )

    if completed.returncode != 0:
        return ""

    return completed.stdout.strip()


def kubectl_config_view_jsonpath(
    context: str,
    jsonpath: str,
    *,
    raise_on_error: bool = False,
    config: Config | None = None,
) -> str:
    """Read a value from kubeconfig using a JSONPath."""
    completed = run_cmd(
        [
            "kubectl",
            "config",
            "view",
            "--raw",
            "--minify",
            "--context",
            context,
            "-o",
            f"jsonpath={jsonpath}",
        ],
        capture_output=True,
        raise_on_error=raise_on_error,
        config=config,
    )

    return completed.stdout.strip()


def node_internal_ip(
    node: str,
    *,
    kube_context: str | None = None,
    raise_on_error: bool = False,
    ipv4_only: bool = True,
    config: Config | None = None,
) -> str:
    """Return a node InternalIP, or ''."""
    completed = kubectl_get(
        ["get", "node", node, "-o", "json"],
        capture_output=True,
        raise_on_error=raise_on_error,
        config=config,
        kube_context=kube_context,
    )

    data = json.loads(completed.stdout)
    addresses = data.get("status", {}).get("addresses", [])

    for address in addresses:
        if address.get("type") != "InternalIP":
            continue

        ip = address.get("address", "")
        if ip and (not ipv4_only or ":" not in ip):
            return ip

    return ""
