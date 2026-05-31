#!/usr/bin/env python3
"""Run registry:2 and stop it when no lab controller is active."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path


REGISTRY_CONTAINER_NAME = os.getenv("REGISTRY_CONTAINER_NAME", "registry-lab")
RUNTIME_ROOT = Path(os.getenv("RUNTIME_ROOT", "/runtime"))
CHECK_INTERVAL_SECONDS = int(os.getenv("REGISTRY_WATCHDOG_INTERVAL_SECONDS", "30"))
EMPTY_GRACE_SECONDS = int(os.getenv("REGISTRY_EMPTY_GRACE_SECONDS", "180"))

registry_proc: subprocess.Popen | None = None
shutdown_requested = False


def docker_running(container: str) -> bool:
    if not container:
        return False

    completed = subprocess.run(
        ["docker", "container", "inspect", "-f", "{{.State.Running}}", container],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return completed.returncode == 0 and completed.stdout.strip().lower() == "true"


def lab_is_active(info_path: Path) -> bool:
    try:
        data = json.loads(info_path.read_text(encoding="utf-8"))
    except Exception:
        return False

    controller = str(data.get("controller_container", "")).strip()
    return bool(controller and docker_running(controller))


def active_labs() -> list[str]:
    if not RUNTIME_ROOT.is_dir():
        return []

    active: list[str] = []

    for info_path in RUNTIME_ROOT.glob("*/info"):
        if lab_is_active(info_path):
            active.append(info_path.parent.name)

    return active

def remove_self() -> None:
    print(
        f"[registry-watchdog] no active labs detected for "
        f"{EMPTY_GRACE_SECONDS}s; removing registry container "
        f"{REGISTRY_CONTAINER_NAME!r}",
        flush=True,
    )

    subprocess.run(
        ["docker", "rm", "-f", REGISTRY_CONTAINER_NAME],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )


def watchdog_loop() -> None:
    empty_since: float | None = None

    while not shutdown_requested:
        labs = active_labs()

        if labs:
            empty_since = None
        else:
            now = time.monotonic()
            if empty_since is None:
                empty_since = now
            elif now - empty_since >= EMPTY_GRACE_SECONDS:
                remove_self()
                return

        time.sleep(CHECK_INTERVAL_SECONDS)


def handle_signal(signum: int, _frame) -> None:
    global shutdown_requested

    shutdown_requested = True

    if registry_proc is not None and registry_proc.poll() is None:
        registry_proc.terminate()


def main() -> int:
    global registry_proc

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    config_file = sys.argv[1] if len(sys.argv) > 1 else "/etc/docker/registry/config.yml"

    thread = threading.Thread(target=watchdog_loop, daemon=True)
    thread.start()

    registry_proc = subprocess.Popen(["registry", "serve", config_file])

    while registry_proc.poll() is None:
        time.sleep(1)

    return int(registry_proc.returncode or 0)


if __name__ == "__main__":
    raise SystemExit(main())
