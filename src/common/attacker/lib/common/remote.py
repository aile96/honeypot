#!/usr/bin/env python3
"""Shared SSH helpers for attacker scripts."""

from __future__ import annotations

import subprocess
from pathlib import Path

SSH_COMMON_OPTIONS = [
    "-o", "StrictHostKeyChecking=no",
    "-o", "UserKnownHostsFile=/dev/null",
    "-o", "LogLevel=ERROR",
]


def ssh_base(host: str, *, user: str = "root", port: int | str = 22, key_path: str | None = None) -> list[str]:
    """Return an ssh command prefix with safe lab defaults."""
    cmd = ["ssh", *SSH_COMMON_OPTIONS, "-p", str(port)]
    if key_path:
        cmd.extend(["-i", key_path])
    cmd.append(f"{user}@{host}")
    return cmd


def run_remote_script(host: str, script: str | Path, *, user: str = "root", port: int | str = 22, args: list[str] | None = None) -> int:
    """Send a local script to a remote host through stdin and execute it with Python."""
    args = args or []
    command = [*ssh_base(host, user=user, port=port), "/usr/bin/env", "python3", "-", *args]
    with open(script, "rb") as handle:
        return subprocess.run(command, stdin=handle).returncode
