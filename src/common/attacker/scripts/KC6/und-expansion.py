#!/usr/bin/env python3
"""Discover the underlay network, with a fallback for minimal node images."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path


def run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, text=True, **kwargs)


def ssh_base(ssh_key: Path) -> list[str]:
    return [
        "ssh",
        "-i",
        str(ssh_key),
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=8",
        "-o",
        "StrictHostKeyChecking=no",
        "-o",
        "UserKnownHostsFile=/dev/null",
        "-o",
        "IdentitiesOnly=yes",
        "-o",
        "LogLevel=ERROR",
    ]


def copy_stream_to_file(completed: subprocess.CompletedProcess[str], out_file: Path) -> bool:
    out_file.write_text(completed.stdout or "", encoding="utf-8")
    return completed.returncode == 0


def run_remote_scan(ssh_key: Path, control_plane: str, out_file: Path) -> bool:
    nmap_script = Path("/opt/caldera/KC2/nmap-enum.sh")
    if not nmap_script.is_file():
        print(f"[!] Remote scan skipped: missing {nmap_script}", file=sys.stderr)
        return False

    base = ssh_base(ssh_key)
    probe = run(
        base
        + [
            "-p",
            "122",
            f"root@{control_plane}",
            "command -v bash >/dev/null 2>&1 && command -v nmap >/dev/null 2>&1",
        ]
    )
    if probe.returncode != 0:
        print("[i] control-plane has no bash/nmap; falling back to local scan")
        return False

    with nmap_script.open("r", encoding="utf-8") as stdin:
        completed = run(
            base
            + [
                "-p",
                "122",
                f"root@{control_plane}",
                '/usr/bin/env bash -s -- "/tmp/data" 1',
            ],
            stdin=stdin,
            capture_output=True,
        )

    if completed.returncode == 0:
        copy_stream_to_file(completed, out_file)
        return True

    print("[!] remote underlay scan failed; falling back to local scan", file=sys.stderr)
    if completed.stderr:
        print(completed.stderr.strip(), file=sys.stderr)
    return False


def run_local_scan(data_path: Path, out_file: Path) -> bool:
    nmap_script = Path("/opt/caldera/KC2/nmap-enum.sh")
    if not nmap_script.is_file():
        print(f"[-] missing {nmap_script}", file=sys.stderr)
        return False

    scan_dir = data_path / "KC6" / "underlay-local"
    scan_dir.mkdir(parents=True, exist_ok=True)
    completed = run([str(nmap_script), str(scan_dir), "0"], capture_output=True)
    out_file.write_text(completed.stdout or "", encoding="utf-8")
    if completed.stderr:
        print(completed.stderr.strip(), file=sys.stderr)
    return completed.returncode == 0


def main() -> int:
    data_path = Path(os.getenv("DATA_PATH", "/tmp/KCData"))
    control_plane = os.getenv("CONTROL_PLANE_NODE", "")
    ssh_key = data_path / "KC6" / "ssh" / "ssh-key"
    out_file = data_path / "KC6" / "underlaynetwork"
    out_file.parent.mkdir(parents=True, exist_ok=True)

    for tool in ("ssh", "nmap", "ip", "awk", "getent"):
        if shutil.which(tool) is None:
            print(f"[-] missing '{tool}'", file=sys.stderr)
            return 1
    if not ssh_key.is_file():
        print(f"[-] SSH key not found: {ssh_key}", file=sys.stderr)
        return 1

    ok = False
    if control_plane:
        ok = run_remote_scan(ssh_key, control_plane, out_file)
    if not ok:
        ok = run_local_scan(data_path, out_file)

    print(f"Done -- Underlay network details saved in {out_file}:")
    if out_file.is_file():
        print(out_file.read_text(encoding="utf-8", errors="replace"), end="")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
