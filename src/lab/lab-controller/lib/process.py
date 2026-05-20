#!/usr/bin/env python3
"""Manage child process lifecycle from the controller entrypoint.

The entrypoint starts long-running child scripts and needs predictable shutdown
behavior. This module wraps process start, signal handling, waiting, and cleanup
so the container can terminate children cleanly when the lab stops."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable, Mapping

from .logging import die, log, warn


def send_signal_to_process_group(proc: subprocess.Popen, sig: int) -> None:
    """Send a signal to the process group created for a child process."""
    if proc.poll() is not None:
        return

    try:
        os.killpg(proc.pid, sig)
    except ProcessLookupError:
        pass
    except PermissionError as exc:
        warn(f"Could not signal process group {proc.pid}: {exc}")


def terminate_process(
    proc: subprocess.Popen,
    name: str,
    *,
    grace_seconds: float = 15.0,
) -> None:
    """Terminate a process group gracefully, then force kill it if needed."""
    if proc.poll() is not None:
        return

    log(f"Terminating {name}...")
    send_signal_to_process_group(proc, signal.SIGTERM)

    deadline = time.monotonic() + grace_seconds
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            log(f"{name} terminated with exit code {proc.returncode}")
            return
        time.sleep(0.2)

    warn(f"{name} did not terminate gracefully; sending SIGKILL.")
    send_signal_to_process_group(proc, signal.SIGKILL)

    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        warn(f"{name} could not be reaped after SIGKILL.")


def wait_for_process(
    proc: subprocess.Popen,
    name: str,
    *,
    should_shutdown: Callable[[], bool] | None = None,
    shutdown_exit_code: Callable[[], int] | None = None,
    grace_seconds: float = 15.0,
) -> int:
    """Wait for a process while optionally reacting to shutdown requests."""
    while True:
        if should_shutdown is not None and should_shutdown():
            terminate_process(proc, name, grace_seconds=grace_seconds)
            return shutdown_exit_code() if shutdown_exit_code is not None else 143

        try:
            return proc.wait(timeout=1)
        except subprocess.TimeoutExpired:
            continue


def run_python_child(
    script_path: str | Path,
    name: str,
    *,
    env: Mapping[str, str] | None = None,
    cwd: str | Path | None = None,
    should_shutdown: Callable[[], bool] | None = None,
    shutdown_exit_code: Callable[[], int] | None = None,
) -> int:
    """Run a Python script in a separate process and return its exit code."""
    path = Path(script_path)

    if not path.exists():
        die(f"{name} not found: {path}")

    if not path.is_file():
        die(f"{name} is not a regular file: {path}")

    log(f"Starting {name}: {path}")

    try:
        proc = subprocess.Popen(
            [sys.executable, str(path)],
            env=dict(env) if env is not None else None,
            cwd=str(cwd) if cwd is not None else None,
            start_new_session=True,
        )
    except OSError as exc:
        die(f"Failed to start {name} at {path}: {exc}")

    exit_code = wait_for_process(
        proc,
        name,
        should_shutdown=should_shutdown,
        shutdown_exit_code=shutdown_exit_code,
    )

    if exit_code == 0:
        log(f"{name} completed successfully.")

    return exit_code
