#!/usr/bin/env python3
"""Handle Docker runtime access from inside the controller container.

The entrypoint uses this module to work with either host-socket Docker access or
a managed Docker daemon. It prepares environment variables, normalizes command
output, and provides small helpers shared by startup and cleanup code."""

from __future__ import annotations

import os
import stat
import subprocess
import time
from pathlib import Path
from typing import Callable

from .logging import die, err, log

DEFAULT_DOCKER_DATA_ROOT = "/var/lib/lab-docker"
DEFAULT_DOCKER_EXEC_ROOT = "/var/run/lab-docker/exec"
DEFAULT_DOCKER_PIDFILE = "/var/run/lab-docker/docker.pid"
DEFAULT_DOCKERD_LOG_FILE = "/tmp/dockerd-entrypoint.log"


def to_text(value: str | bytes | None) -> str:
    """Convert subprocess output to text safely."""
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode(errors="replace")
    return value


def is_unix_socket(path: Path) -> bool:
    """Return True if path exists and is a Unix socket."""
    try:
        mode = path.stat().st_mode
    except FileNotFoundError:
        return False
    return stat.S_ISSOCK(mode)


def require_existing_unix_socket(socket_path: Path) -> None:
    """Validate that socket_path exists and is a Unix socket."""
    if not socket_path.exists():
        die(
            f"Docker socket not found at {socket_path}. "
            "In HOST_SOCKET=true mode, mount the host socket there."
        )

    if not is_unix_socket(socket_path):
        die(f"{socket_path} exists but is not a Unix socket.")

    log(f"Verified Docker socket at {socket_path}")


def ensure_socket_absent(socket_path: Path) -> None:
    """Ensure the Docker socket path is absent before starting internal dockerd."""
    if socket_path.exists() or socket_path.is_symlink():
        die(
            f"{socket_path} already exists. Refusing to start internal dockerd "
            "because the socket may be host-mounted or owned by another process."
        )


def docker_env(socket_path: Path, *, base_env: dict[str, str] | None = None) -> dict[str, str]:
    """Build an environment that points Docker CLI at socket_path."""
    env = dict(os.environ if base_env is None else base_env)
    env["DOCKER_HOST"] = f"unix://{socket_path}"
    env["DOCKER_SOCKET_PATH"] = str(socket_path)
    return env


def print_dockerd_logs(log_path: Path, max_bytes: int = 64 * 1024) -> None:
    """Print the tail of dockerd logs to stderr for diagnostics."""
    if not log_path.exists():
        err(f"dockerd log file does not exist: {log_path}")
        return

    try:
        with log_path.open("rb") as log_file:
            log_file.seek(0, os.SEEK_END)
            size = log_file.tell()
            log_file.seek(max(0, size - max_bytes))
            data = log_file.read().decode(errors="replace")
    except OSError as exc:
        err(f"Could not read dockerd logs from {log_path}: {exc}")
        return

    import sys

    sys.stderr.write("\n========== dockerd log tail ==========\n")
    sys.stderr.write(data)
    if not data.endswith("\n"):
        sys.stderr.write("\n")
    sys.stderr.write("======== end dockerd log tail ========\n")
    sys.stderr.flush()


def start_internal_dockerd(
    socket_path: Path,
    *,
    data_root: str | Path = DEFAULT_DOCKER_DATA_ROOT,
    exec_root: str | Path = DEFAULT_DOCKER_EXEC_ROOT,
    pidfile: str | Path = DEFAULT_DOCKER_PIDFILE,
    log_file: str | Path = DEFAULT_DOCKERD_LOG_FILE,
    insecure_registries: list[str] | None = None,
) -> subprocess.Popen:
    """Start an internal Docker daemon listening on socket_path."""
    ensure_socket_absent(socket_path)

    data_root = Path(data_root)
    exec_root = Path(exec_root)
    pidfile = Path(pidfile)
    log_file = Path(log_file)

    socket_path.parent.mkdir(parents=True, exist_ok=True)
    data_root.mkdir(parents=True, exist_ok=True)
    exec_root.mkdir(parents=True, exist_ok=True)
    pidfile.parent.mkdir(parents=True, exist_ok=True)
    log_file.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        "dockerd",
        f"--host=unix://{socket_path}",
        f"--data-root={data_root}",
        f"--exec-root={exec_root}",
        f"--pidfile={pidfile}",
    ]
    for registry in insecure_registries or []:
        registry = registry.strip()
        if registry:
            cmd.append(f"--insecure-registry={registry}")

    log("Starting internal dockerd.")
    log(f"dockerd socket: {socket_path}")
    log(f"dockerd data-root: {data_root}")
    log(f"dockerd exec-root: {exec_root}")
    log(f"dockerd pidfile: {pidfile}")
    log(f"dockerd logs: {log_file}")
    if insecure_registries:
        log(f"dockerd insecure registries: {', '.join(insecure_registries)}")

    try:
        handle = log_file.open("ab", buffering=0)
    except OSError as exc:
        die(f"Could not open dockerd log file {log_file}: {exc}")

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=handle,
            stderr=subprocess.STDOUT,
            env=os.environ.copy(),
            start_new_session=True,
        )
    except FileNotFoundError:
        handle.close()
        die("dockerd executable not found. Install Docker Engine inside the image.")
    except OSError as exc:
        handle.close()
        die(f"Failed to start dockerd: {exc}")

    return proc


def docker_info_once(socket_path: Path, timeout_seconds: float) -> subprocess.CompletedProcess[str]:
    """Run docker info once against socket_path."""
    try:
        return subprocess.run(
            ["docker", "info"],
            env=docker_env(socket_path),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout_seconds,
            check=False,
        )
    except FileNotFoundError:
        die("docker CLI not found. Install Docker CLI inside the image.")
    except subprocess.TimeoutExpired as exc:
        return subprocess.CompletedProcess(
            args=["docker", "info"],
            returncode=124,
            stdout=to_text(exc.stdout),
            stderr=to_text(exc.stderr) or f"`docker info` timed out after {timeout_seconds} seconds.",
        )


def wait_for_docker_ready(
    socket_path: Path,
    timeout_seconds: float,
    *,
    dockerd_proc: subprocess.Popen | None = None,
    dockerd_log_file: str | Path = DEFAULT_DOCKERD_LOG_FILE,
    should_shutdown: Callable[[], bool] | None = None,
    shutdown_exit_code: Callable[[], int] | None = None,
) -> None:
    """Wait until docker info succeeds or fail with diagnostics."""
    deadline = time.monotonic() + timeout_seconds
    last_error = ""

    log(f"Waiting for Docker to become ready; timeout={timeout_seconds}s")

    while time.monotonic() < deadline:
        if should_shutdown is not None and should_shutdown():
            die("Shutdown requested while waiting for Docker.", exit_code=(shutdown_exit_code() if shutdown_exit_code else 143))

        if dockerd_proc is not None and dockerd_proc.poll() is not None:
            print_dockerd_logs(Path(dockerd_log_file))
            die(f"dockerd exited before becoming ready. Exit code: {dockerd_proc.returncode}")

        remaining = max(1.0, min(5.0, deadline - time.monotonic()))
        result = docker_info_once(socket_path, timeout_seconds=remaining)

        if result.returncode == 0:
            log("Docker is ready.")
            return

        last_error = (to_text(result.stderr) or to_text(result.stdout)).strip()
        time.sleep(1)

    if dockerd_proc is not None:
        print_dockerd_logs(Path(dockerd_log_file))

    die(
        "Docker did not become ready within "
        f"{timeout_seconds} seconds. Last `docker info` error: {last_error or 'no output'}"
    )
