#!/usr/bin/env python3
"""Bootstrap the lab-controller container and manage child processes.

The entrypoint handles container lifecycle concerns only: it validates the Docker
access mode, starts or reuses Docker access, launches the configured pipeline and
controller scripts, keeps the container alive when requested, and performs
best-effort cleanup during shutdown. It deliberately does not load target CONFIG."""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional


APP_ROOT = Path(os.environ.get("APP_ROOT", "/app")).resolve()
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from lib import (  # noqa: E402
    DEFAULT_DOCKER_DATA_ROOT,
    DEFAULT_DOCKERD_LOG_FILE,
    cleanup_docker_compose_stack,
    cleanup_kind_cluster,
    docker_env,
    log,
    parse_bool_value,
    parse_positive_float_value,
    require_existing_unix_socket,
    send_signal_to_process_group,
    start_internal_dockerd,
    terminate_process,
    wait_for_docker_ready,
    warn,
)


DEFAULT_CODE_ROOT = "/workdir/code"
DEFAULT_ENV_FILE = "/workdir/code/conf-files/variables.py"
DEFAULT_FIRST_SCRIPT = "/app/start_lab.py"
DEFAULT_SECOND_SCRIPT = "/start_controller.py"
DEFAULT_DOCKER_SOCKET_PATH = "/var/run/docker.sock"
DEFAULT_RUNTIME_DIR = "/res/runtime"
DEFAULT_IDLE_SLEEP_SECONDS = "3600"
DEFAULT_DOCKER_READY_TIMEOUT = "60"
DEFAULT_COMPOSE_PROJECT_NAME = "honeypot-underlay"

ENTRYPOINT_ENV_DEFAULTS: dict[str, str] = {
    "HOST_SOCKET": "false",
    "DOCKER_SOCKET_PATH": DEFAULT_DOCKER_SOCKET_PATH,
    "DOCKER_DATA_ROOT": DEFAULT_DOCKER_DATA_ROOT,
    "DOCKER_READY_TIMEOUT": DEFAULT_DOCKER_READY_TIMEOUT,
    "IDLE_SLEEP_SECONDS": DEFAULT_IDLE_SLEEP_SECONDS,
    "RUNTIME_DIR": DEFAULT_RUNTIME_DIR,
    "FIRST_SCRIPT": DEFAULT_FIRST_SCRIPT,
    "SECOND_SCRIPT": DEFAULT_SECOND_SCRIPT,
}

REQUIRED_ENTRYPOINT_ENV_KEYS = tuple(ENTRYPOINT_ENV_DEFAULTS.keys())
ENTRYPOINT_BOOL_KEYS = (
    "HOST_SOCKET",
)
ENTRYPOINT_POSITIVE_FLOAT_KEYS = (
    "DOCKER_READY_TIMEOUT",
    "IDLE_SLEEP_SECONDS",
)
ENTRYPOINT_ABSOLUTE_PATH_KEYS = (
    "DOCKER_SOCKET_PATH",
    "FIRST_SCRIPT",
    "SECOND_SCRIPT",
    "RUNTIME_DIR",
)


shutdown_requested = False
signal_exit_code = 0
active_process: Optional[subprocess.Popen] = None


def handle_signal(signum: int, _frame) -> None:
    """Handle SIGTERM/SIGINT and forward the signal to the active child."""
    global shutdown_requested, signal_exit_code

    shutdown_requested = True
    signal_exit_code = 128 + signum
    warn(f"Received signal {signum}; shutting down.")

    if active_process is not None and active_process.poll() is None:
        send_signal_to_process_group(active_process, signum)


def install_signal_handlers() -> None:
    """Install graceful shutdown signal handlers."""
    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)


def should_shutdown() -> bool:
    """Return True after SIGTERM/SIGINT."""
    return shutdown_requested


def current_signal_exit_code() -> int:
    """Return the signal-derived exit code."""
    return signal_exit_code or 143


def env_value(name: str, default: str | None = None) -> str:
    """Return a non-empty environment value, applying an optional default."""
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        if default is None:
            raise RuntimeError(f"Missing required entrypoint environment variable: {name}")
        return default
    return raw


def load_entrypoint_runtime() -> dict[str, object]:
    """Load only the runtime values this entrypoint directly uses."""
    runtime: dict[str, object] = {}

    for name in REQUIRED_ENTRYPOINT_ENV_KEYS:
        runtime[name] = env_value(name, ENTRYPOINT_ENV_DEFAULTS.get(name))

    for name in ENTRYPOINT_BOOL_KEYS:
        runtime[name] = parse_bool_value(runtime[name], name=name)

    for name in ENTRYPOINT_POSITIVE_FLOAT_KEYS:
        runtime[name] = parse_positive_float_value(runtime[name], name=name)

    for name in ENTRYPOINT_ABSOLUTE_PATH_KEYS:
        path = Path(str(runtime[name])).expanduser()
        if not path.is_absolute():
            raise RuntimeError(f"{name} must be an absolute path, got: {path}")
        runtime[name] = path.resolve()

    docker_data_root = Path(str(runtime["DOCKER_DATA_ROOT"])).expanduser()
    if not docker_data_root.is_absolute():
        raise RuntimeError(f"DOCKER_DATA_ROOT must be an absolute path, got: {docker_data_root}")
    runtime["DOCKER_DATA_ROOT"] = docker_data_root.resolve()

    return runtime


def build_child_env(socket_path: Path) -> dict[str, str]:
    """Build child environment without loading lab CONFIG in this process."""
    env = docker_env(socket_path)
    env.setdefault("PYTHONPATH", str(APP_ROOT))
    env.setdefault("ENV_FILE", DEFAULT_ENV_FILE)
    env.setdefault("CODE_ROOT", DEFAULT_CODE_ROOT)
    return env


def run_child_script(script_path: Path, name: str, socket_path: Path) -> None:
    """Run a child Python script and fail if it exits non-zero."""
    global active_process

    if not script_path.exists():
        raise SystemExit(f"{name} not found: {script_path}")

    log(f"Starting {name}: {script_path}")

    try:
        active_process = subprocess.Popen(
            [sys.executable, str(script_path)],
            env=build_child_env(socket_path),
            start_new_session=True,
        )
    except OSError as exc:
        raise SystemExit(f"Failed to start {name} at {script_path}: {exc}") from exc

    try:
        while True:
            if should_shutdown():
                terminate_process(active_process, name)
                raise SystemExit(current_signal_exit_code())

            try:
                exit_code = active_process.wait(timeout=1)
                break
            except subprocess.TimeoutExpired:
                continue
    finally:
        active_process = None

    if exit_code != 0:
        raise SystemExit(f"{name} exited with non-zero exit code: {exit_code}")

    log(f"{name} completed successfully.")


def idle_forever(sleep_seconds: float) -> None:
    """Keep the container alive with a lightweight signal-aware loop."""
    log("Both scripts completed successfully. Entering idle loop.")
    sleep_chunk = min(sleep_seconds, 5.0)

    while not shutdown_requested:
        time.sleep(sleep_chunk)

    log("Idle loop interrupted by shutdown signal.")
    raise SystemExit(signal_exit_code or 0)


def cleanup_runtime_dir(runtime_dir: Path) -> None:
    """Remove all files below RUNTIME_DIR on graceful controller shutdown."""
    runtime_dir = runtime_dir.resolve()

    if str(runtime_dir) in {"/", ""}:
        warn(f"Refusing to clean unsafe RUNTIME_DIR: {runtime_dir}")
        return

    if not runtime_dir.exists():
        log(f"Runtime directory not present; nothing to clean: {runtime_dir}")
        return

    if not runtime_dir.is_dir():
        warn(f"RUNTIME_DIR is not a directory; skipping cleanup: {runtime_dir}")
        return

    removed = 0
    for item in runtime_dir.iterdir():
        try:
            if item.is_dir() and not item.is_symlink():
                shutil.rmtree(item)
            else:
                item.unlink()
            removed += 1
        except FileNotFoundError:
            continue
        except Exception as exc:
            warn(f"Could not remove runtime item {item}: {exc}")

    runtime_dir.mkdir(parents=True, exist_ok=True)
    log(f"Cleaned runtime directory {runtime_dir} ({removed} top-level item(s) removed).")


def cleanup_values() -> dict[str, str]:
    """Return minimal cleanup values sourced only from process environment.

    This deliberately does not load variables.py. It only gives cleanup helpers
    enough information to find the target compose file and likely Kind cluster.
    """
    values: dict[str, str] = {}

    for name in (
        "CODE_ROOT",
        "CLUSTER_PROFILE",
        "KUBE_CONTEXT",
        "COMPOSE_PROJECT_NAME",
        "HOST_SOCKET",
    ):
        raw = os.environ.get(name)
        if raw is not None and raw.strip() != "":
            values[name] = raw

    values.setdefault("CODE_ROOT", DEFAULT_CODE_ROOT)
    values.setdefault("COMPOSE_PROJECT_NAME", DEFAULT_COMPOSE_PROJECT_NAME)
    return values


def main() -> int:
    """Prepare Docker access, run configured scripts, then keep container alive."""
    install_signal_handlers()

    runtime = load_entrypoint_runtime()

    host_socket_mode = bool(runtime["HOST_SOCKET"])
    socket_path = Path(runtime["DOCKER_SOCKET_PATH"])
    ready_timeout = float(runtime["DOCKER_READY_TIMEOUT"])
    docker_data_root = Path(runtime["DOCKER_DATA_ROOT"])
    idle_sleep_seconds = float(runtime["IDLE_SLEEP_SECONDS"])
    runtime_dir = Path(runtime["RUNTIME_DIR"])
    first_script = Path(runtime["FIRST_SCRIPT"])
    second_script = Path(runtime["SECOND_SCRIPT"])
    dockerd_proc: Optional[subprocess.Popen] = None

    log("Entrypoint starting.")
    log(f"ENV_FILE for child scripts: {os.environ.get('ENV_FILE', DEFAULT_ENV_FILE)}")
    log(f"CODE_ROOT for child scripts: {os.environ.get('CODE_ROOT', DEFAULT_CODE_ROOT)}")
    log(f"HOST_SOCKET mode: {host_socket_mode}")
    log(f"FIRST_SCRIPT: {first_script}")
    log(f"SECOND_SCRIPT: {second_script}")

    try:
        if host_socket_mode:
            log("Using host Docker socket. Internal dockerd will not be started.")
            require_existing_unix_socket(socket_path)
            wait_for_docker_ready(
                socket_path,
                ready_timeout,
                should_shutdown=should_shutdown,
                shutdown_exit_code=current_signal_exit_code,
            )
        else:
            log("Using internal Docker daemon.")
            dockerd_proc = start_internal_dockerd(socket_path, data_root=docker_data_root)
            wait_for_docker_ready(
                socket_path,
                ready_timeout,
                dockerd_proc=dockerd_proc,
                dockerd_log_file=DEFAULT_DOCKERD_LOG_FILE,
                should_shutdown=should_shutdown,
                shutdown_exit_code=current_signal_exit_code,
            )

        run_child_script(first_script, "FIRST_SCRIPT", socket_path)
        run_child_script(second_script, "SECOND_SCRIPT", socket_path)

        idle_forever(idle_sleep_seconds)
        return 0

    finally:
        cleanup_config = cleanup_values()
        try:
            cleanup_docker_compose_stack(socket_path, cleanup_config)
        except Exception as exc:
            warn(f"Error during Docker Compose cleanup: {exc}")

        try:
            cleanup_kind_cluster(cleanup_config, socket_path)
        except Exception as exc:
            warn(f"Error during Kind cluster cleanup: {exc}")

        cleanup_runtime_dir(runtime_dir)

        if dockerd_proc is not None and dockerd_proc.poll() is None:
            terminate_process(dockerd_proc, "dockerd")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)