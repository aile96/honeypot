#!/usr/bin/env python3
"""Stop and remove one lab controller and shared resources when unused."""

from __future__ import annotations

import argparse
import shutil
import sys
import time
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
LIB_ROOT = PROJECT_ROOT / "src" / "lab" / "lib"
CONFIG_FILE = PROJECT_ROOT / "configuration.conf"
DEFAULT_NETWORK = "lab"

if str(LIB_ROOT.parent) not in sys.path:
    sys.path.insert(0, str(LIB_ROOT.parent))

from lib import (  # noqa: E402
    ConfigError,
    DockerError,
    check_docker,
    container_exists,
    container_running,
    load_project_config,
    remove_registry_if_unused,
    remove_network_if_unused,
    run,
    stop_container,
)


def log(message: str) -> None:
    print(f"[INFO] {message}", flush=True)


def fail(message: str) -> None:
    raise SystemExit(f"[ERR ] {message}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Stop and remove a honeypot lab controller")
    parser.add_argument("lab_name", nargs="?", help="Lab name to remove; defaults to configuration.conf")
    parser.add_argument("--config", default=str(CONFIG_FILE), help="Path to configuration.conf TOML file")
    return parser.parse_args()


def signal_controller(name: str) -> None:
    if not container_running(name):
        return
    run(
        [
            "docker",
            "exec",
            name,
            "sh",
            "-lc",
            "pid=\"$(ps -eo pid=,args= | awk '/entrypoint[.]py/ {print $1; exit}')\"; test -z \"$pid\" || kill -TERM \"$pid\"",
        ],
        check=False,
        quiet=True,
    )


def remove_runtime_dir(path: Path) -> None:
    if not path.exists():
        return
    try:
        shutil.rmtree(path)
        return
    except OSError:
        pass
    run(
        [
            "docker",
            "run",
            "--rm",
            "-v",
            f"{path.parent}:/runtime",
            "alpine:3.20",
            "rm",
            "-rf",
            f"/runtime/{path.name}",
        ],
        check=False,
        quiet=True,
    )


def main() -> int:
    args = parse_args()
    try:
        config = load_project_config(args.config)
        lab_name = args.lab_name or str(config.get("LAB_NAME", "")).strip()
        if not lab_name:
            fail("LAB_NAME is required in configuration.conf or as CLI argument")
        controller = str(config.get("CONTROLLER_CONTAINER_NAME") or f"{lab_name}-controller")
        runtime_dir = PROJECT_ROOT / "res" / "runtime" / lab_name

        check_docker()
        if not container_exists(controller):
            log(f"Controller container {controller!r} does not exist; continuing cleanup.")
            log(f"Removing runtime directory {runtime_dir}.")
            remove_runtime_dir(runtime_dir)
            remove_registry_if_unused(PROJECT_ROOT, "registry-lab")
            remove_network_if_unused(PROJECT_ROOT, DEFAULT_NETWORK)
            log(f"Cleanup completed for lab {lab_name!r}.")
            return 0

        try:
            log(f"Signalling controller {controller!r}.")
            signal_controller(controller)
            deadline = time.monotonic() + 60
            while container_running(controller) and time.monotonic() < deadline:
                time.sleep(1)
            stop_container(controller, timeout=20)
        finally:
            log(f"Removing runtime directory {runtime_dir}.")
            remove_runtime_dir(runtime_dir)
            remove_registry_if_unused(PROJECT_ROOT, "registry-lab")
            remove_network_if_unused(PROJECT_ROOT, DEFAULT_NETWORK)

        log(f"Cleanup completed for lab {lab_name!r}.")
        return 0
    except (ConfigError, DockerError, RuntimeError) as exc:
        fail(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
