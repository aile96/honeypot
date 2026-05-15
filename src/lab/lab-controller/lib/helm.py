#!/usr/bin/env python3
"""Provide Helm command helpers for deployment and recovery paths.

The module contains small wrappers around Helm operations that the hooks need,
especially stale or pending release cleanup before a new Skaffold deployment. It
keeps Helm-specific command construction out of target hook bodies."""

from __future__ import annotations

import json
import subprocess

from .command import run_cmd
from .config import Config
from .logging import warn


def helm_ctx(
    kube_context: str,
    args: list[str],
    *,
    check: bool = True,
    capture_output: bool = False,
    quiet: bool = False,
    config: Config | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run helm with an explicit Kubernetes context."""
    return run_cmd(
        ["helm", "--kube-context", kube_context, *args],
        check=check,
        capture_output=capture_output,
        quiet=quiet,
        config=config,
    )


def clear_pending_helm_release(
    kube_context: str,
    release: str,
    namespace: str,
    *,
    config: Config | None = None,
) -> None:
    """Uninstall a Helm release left in a pending state by an interrupted run."""
    result = helm_ctx(
        kube_context,
        ["status", release, "-n", namespace, "-o", "json"],
        check=False,
        capture_output=True,
        quiet=True,
        config=config,
    )
    if result.returncode != 0:
        return

    try:
        status = json.loads(result.stdout or "{}").get("info", {}).get("status", "")
    except json.JSONDecodeError:
        return

    if not str(status).startswith("pending-"):
        return

    warn(f"Release {release!r} is {status}; uninstalling before redeploy.")
    helm_ctx(
        kube_context,
        ["uninstall", release, "-n", namespace, "--no-hooks"],
        check=False,
        config=config,
    )
