#!/usr/bin/env python3
"""Shared process helpers for attacker Python entrypoints."""

from __future__ import annotations

import os
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Iterable, Mapping


def run(args: Iterable[str], *, env: Mapping[str, str] | None = None, check: bool = True) -> subprocess.CompletedProcess[str]:
    """Run a command while inheriting standard streams by default."""
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)
    return subprocess.run(list(args), env=merged_env, check=check, text=True)


def run_embedded_bash(script: str, argv: list[str] | None = None) -> int:
    """Execute embedded Bash content from a temporary file and return its exit code."""
    argv = argv or []
    lib_dir = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env.setdefault("ATTACKER_LIB_DIR", str(lib_dir))
    env["PYTHONPATH"] = f"{lib_dir}:{env['PYTHONPATH']}" if env.get("PYTHONPATH") else str(lib_dir)

    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".sh", delete=False) as handle:
        handle.write(script)
        script_path = handle.name
    try:
        Path(script_path).chmod(Path(script_path).stat().st_mode | stat.S_IXUSR)
        completed = subprocess.run(["/usr/bin/env", "bash", script_path, *argv], env=env, text=True)
        return int(completed.returncode)
    finally:
        try:
            os.unlink(script_path)
        except FileNotFoundError:
            pass


def main_embedded(script: str) -> None:
    """Run embedded Bash and exit with the same status."""
    raise SystemExit(run_embedded_bash(script, sys.argv[1:]))
