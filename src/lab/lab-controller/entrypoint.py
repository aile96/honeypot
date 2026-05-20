#!/usr/bin/env python3
"""Bootstrap the lab-controller container and manage child processes."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
import tomllib
from pathlib import Path
from typing import Any, Optional


APP_ROOT = Path("/app").resolve()
CONFIG_FILE = Path("/runtime/config.toml")

if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from lib import (  # noqa: E402
    DEFAULT_DOCKERD_LOG_FILE,
    cleanup_docker_compose_stack,
    cleanup_kind_cluster,
    docker_env,
    log,
    parse_bool_value,
    parse_positive_float_value,
    require_existing_unix_socket,
    registry_endpoint,
    send_signal_to_process_group,
    start_internal_dockerd,
    terminate_process,
    wait_for_docker_ready,
    warn,
)


REQUIRED_ENTRYPOINT_CONFIG_KEYS = (
    "HOST_SOCKET",
    "DOCKER_SOCKET_PATH",
    "DOCKER_DATA_ROOT",
    "DOCKER_READY_TIMEOUT",
    "FIRST_SCRIPT",
    "SECOND_SCRIPT",
    "PROXY_SCRIPT",
    "IDLE_SLEEP_SECONDS",
    "RUNTIME_DIR",
)
ENTRYPOINT_BOOL_KEYS = ("HOST_SOCKET",)
ENTRYPOINT_POSITIVE_FLOAT_KEYS = ("DOCKER_READY_TIMEOUT", "IDLE_SLEEP_SECONDS")
ENTRYPOINT_ABSOLUTE_PATH_KEYS = (
    "DOCKER_SOCKET_PATH",
    "FIRST_SCRIPT",
    "SECOND_SCRIPT",
    "PROXY_SCRIPT",
    "RUNTIME_DIR",
    "DOCKER_DATA_ROOT",
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
    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)


def should_shutdown() -> bool:
    return shutdown_requested


def current_signal_exit_code() -> int:
    return signal_exit_code or 143


def read_runtime_config(path: Path = CONFIG_FILE) -> dict[str, Any]:
    """Read the controller runtime config from the fixed mounted path."""
    if not path.is_file():
        raise RuntimeError(f"Runtime config file not found: {path}")
    with path.open("rb") as handle:
        data = tomllib.load(handle)
    table = data.get("config", data)
    if not isinstance(table, dict):
        raise RuntimeError(f"Runtime config must contain a [config] table: {path}")
    return {str(key): value for key, value in table.items() if not isinstance(value, dict)}


def require_config(config: dict[str, Any], name: str) -> Any:
    value = config.get(name)
    if value is None or str(value).strip() == "":
        raise RuntimeError(f"Missing required entrypoint config value: {name}")
    return value


def load_entrypoint_runtime() -> dict[str, object]:
    """Load only the runtime values this entrypoint directly uses."""
    runtime = read_runtime_config()

    missing = [name for name in REQUIRED_ENTRYPOINT_CONFIG_KEYS if name not in runtime or str(runtime[name]).strip() == ""]
    if missing:
        raise RuntimeError("Missing required entrypoint config value(s): " + ", ".join(missing))

    for name in ENTRYPOINT_BOOL_KEYS:
        runtime[name] = parse_bool_value(runtime[name], name=name)

    for name in ENTRYPOINT_POSITIVE_FLOAT_KEYS:
        runtime[name] = parse_positive_float_value(runtime[name], name=name)

    for name in ENTRYPOINT_ABSOLUTE_PATH_KEYS:
        path = Path(str(runtime[name])).expanduser()
        if not path.is_absolute():
            raise RuntimeError(f"{name} must be an absolute path, got: {path}")
        runtime[name] = path.resolve()

    return runtime


def build_child_env(socket_path: Path) -> dict[str, str]:
    """Build the minimal child environment."""
    env = docker_env(socket_path)
    env.setdefault("PYTHONPATH", str(APP_ROOT))
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
    """Leave RUNTIME_DIR in place so restarted controllers reload the same CONFIG."""
    log(f"Leaving runtime directory in place for restart persistence: {runtime_dir.resolve()}")


def cleanup_values(config: dict[str, Any]) -> dict[str, str]:
    """Return cleanup values from the runtime config."""
    values: dict[str, str] = {}
    for name in (
        "CODE_ROOT",
        "LAB_NAME",
        "CLUSTER_PROFILE",
        "KUBE_CONTEXT",
        "COMPOSE_PROJECT_NAME",
        "COMPOSE_FILE",
        "CP_NETWORK",
        "RUNTIME_DIR",
        "HOST_SOCKET",
    ):
        if name in config and config[name] is not None and str(config[name]).strip() != "":
            values[name] = str(config[name])

    lab_name = values.get("LAB_NAME") or values.get("CLUSTER_PROFILE") or "honeypotlab"
    values["LAB_NAME"] = lab_name
    values["CLUSTER_PROFILE"] = lab_name
    values.setdefault("CODE_ROOT", "/workdir/code")
    values.setdefault("RUNTIME_DIR", f"/res/runtime/{lab_name}")
    values.setdefault("COMPOSE_FILE", str(Path(values["RUNTIME_DIR"]) / "generated" / "compose.yaml"))
    values.setdefault("COMPOSE_PROJECT_NAME", f"honeypot-{lab_name}")
    values.setdefault("CP_NETWORK", "lab")
    return values


def start_proxy_process(proxy_script: Path, socket_path: Path) -> Optional[subprocess.Popen]:
    """Start the controller HTTP proxy as a background child process."""
    if not proxy_script.exists():
        warn(f"Proxy script not found; controller proxy disabled: {proxy_script}")
        return None

    log(f"Starting controller proxy: {proxy_script}")
    try:
        return subprocess.Popen(
            [sys.executable, str(proxy_script)],
            env=build_child_env(socket_path),
            start_new_session=True,
        )
    except OSError as exc:
        warn(f"Failed to start controller proxy at {proxy_script}: {exc}")
        return None


def insecure_registry_endpoints(config: dict[str, Any]) -> list[str]:
    """Return HTTP registry endpoints that the internal Docker daemon must allow."""
    endpoints = [
        str(config.get("REGISTRY_CACHE_ENDPOINT", "")).strip(),
        registry_endpoint(config),
    ]
    unique: list[str] = []
    seen: set[str] = set()
    for endpoint in endpoints:
        if endpoint and endpoint not in seen:
            unique.append(endpoint)
            seen.add(endpoint)
    return unique


def main() -> int:
    """Prepare Docker access, run configured scripts, then keep container alive."""
    install_signal_handlers()

    config = read_runtime_config()
    runtime = load_entrypoint_runtime()

    host_socket_mode = bool(runtime["HOST_SOCKET"])
    socket_path = Path(runtime["DOCKER_SOCKET_PATH"])
    ready_timeout = float(runtime["DOCKER_READY_TIMEOUT"])
    docker_data_root = Path(runtime["DOCKER_DATA_ROOT"])
    idle_sleep_seconds = float(runtime["IDLE_SLEEP_SECONDS"])
    runtime_dir = Path(runtime["RUNTIME_DIR"])
    first_script = Path(runtime["FIRST_SCRIPT"])
    second_script = Path(runtime["SECOND_SCRIPT"])
    proxy_script = Path(runtime["PROXY_SCRIPT"])
    dockerd_proc: Optional[subprocess.Popen] = None
    proxy_proc: Optional[subprocess.Popen] = None

    log("Entrypoint starting.")
    log(f"Runtime config: {CONFIG_FILE}")
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
            dockerd_proc = start_internal_dockerd(
                socket_path,
                data_root=docker_data_root,
                insecure_registries=insecure_registry_endpoints(config),
            )
            wait_for_docker_ready(
                socket_path,
                ready_timeout,
                dockerd_proc=dockerd_proc,
                dockerd_log_file=DEFAULT_DOCKERD_LOG_FILE,
                should_shutdown=should_shutdown,
                shutdown_exit_code=current_signal_exit_code,
            )

        if host_socket_mode:
            log("HOST_SOCKET=true; controller proxy will not be started.")
        else:
            proxy_proc = start_proxy_process(proxy_script, socket_path)
        run_child_script(first_script, "FIRST_SCRIPT", socket_path)
        run_child_script(second_script, "SECOND_SCRIPT", socket_path)

        idle_forever(idle_sleep_seconds)
        return 0

    finally:
        cleanup_config = cleanup_values(config)
        try:
            cleanup_docker_compose_stack(socket_path, cleanup_config)
        except Exception as exc:
            warn(f"Error during Docker Compose cleanup: {exc}")

        try:
            cleanup_kind_cluster(cleanup_config, socket_path)
        except Exception as exc:
            warn(f"Error during Kind cluster cleanup: {exc}")

        cleanup_runtime_dir(runtime_dir)

        if proxy_proc is not None and proxy_proc.poll() is None:
            terminate_process(proxy_proc, "controller proxy")

        if dockerd_proc is not None and dockerd_proc.poll() is None:
            terminate_process(dockerd_proc, "dockerd")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)
