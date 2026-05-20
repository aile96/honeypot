#!/usr/bin/env python3
"""KC02 restore: undo the mongodb-nwdaf patch, then run the generic restore."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
import runpy


def log(*parts: object) -> None:
    print("[5gcore-restore-kc2]", *parts, flush=True)


def kubectl(*args: str) -> None:
    command = ["kubectl"]
    context = os.getenv("KUBE_CONTEXT", "").strip()
    if context:
        command.extend(["--context", context])
    command.extend(args)
    completed = subprocess.run(command, text=True, check=False)
    if completed.returncode != 0:
        log("command failed", " ".join(command), "exit", completed.returncode)


namespace = os.getenv("CORE_NAMESPACE", os.getenv("FREE5GC_NAMESPACE", "free5gc"))
log("undoing mongodb-nwdaf patch if a controller revision is available")
kubectl("-n", namespace, "rollout", "undo", "statefulset/mongodb-nwdaf")
kubectl("-n", namespace, "rollout", "restart", "statefulset/mongodb-nwdaf")

runpy.run_path(str(Path(__file__).with_name("HOOK_RESTORE.py")), run_name="__main__", init_globals=globals())
