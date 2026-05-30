#!/usr/bin/env python3
"""Best-effort restore for 5Gcore kill chains."""

from __future__ import annotations

import os
import shlex
import subprocess


def log(*parts: object) -> None:
    print("[5gcore-restore]", *parts, flush=True)


def env(*names: str, default: str) -> str:
    for name in names:
        value = os.getenv(name, "").strip()
        if value:
            return value
    return default


def canonical_kc_key(value: str) -> str:
    raw = str(value or "").strip().upper()
    if raw.startswith("KC") and raw[2:].isdigit():
        return f"KC{int(raw[2:])}"
    return raw or "KC"


def format_command(command: list[str]) -> str:
    return " ".join(shlex.quote(part) for part in command)


def run(command: list[str], *, timeout: int = 120, log_fail: bool = True) -> subprocess.CompletedProcess[str]:
    try:
        completed = subprocess.run(command, text=True, capture_output=True, check=False, timeout=timeout)
    except Exception as exc:  # noqa: BLE001 - restore should keep going.
        if log_fail:
            log("command failed", format_command(command), repr(exc))
        return subprocess.CompletedProcess(command, 1, "", repr(exc))
    if completed.returncode != 0 and log_fail:
        details = (completed.stderr or completed.stdout or "").strip()
        if len(details) > 500:
            details = details[:500] + "..."
        log("command failed", format_command(command), "exit", completed.returncode, details)
    return completed


def kubectl_cmd(*args: str) -> list[str]:
    command = ["kubectl"]
    context = os.getenv("KUBE_CONTEXT", "").strip()
    if context:
        command.extend(["--context", context])
    command.extend(args)
    return command


def kubectl(*args: str, timeout: int = 120, log_fail: bool = True) -> bool:
    return run(kubectl_cmd(*args), timeout=timeout, log_fail=log_fail).returncode == 0


def docker_container_names() -> set[str]:
    completed = run(["docker", "ps", "-a", "--format", "{{.Names}}"], timeout=20, log_fail=False)
    if completed.returncode != 0:
        return set()
    return {line.strip() for line in completed.stdout.splitlines() if line.strip()}


def running_docker_container_names() -> set[str]:
    completed = run(["docker", "ps", "--format", "{{.Names}}"], timeout=20, log_fail=False)
    if completed.returncode != 0:
        return set()
    return {line.strip() for line in completed.stdout.splitlines() if line.strip()}


def resolve_container_name(base: str) -> str:
    names = docker_container_names()
    lab_name = env("LAB_NAME", "CLUSTER_PROFILE", default="honeypotlab")
    candidates = [base]
    if lab_name and not base.endswith(f"-{lab_name}"):
        candidates.append(f"{base}-{lab_name}")
    for candidate in candidates:
        if candidate in names:
            return candidate
    return candidates[-1]


def docker_exec_attacker_shell(script: str) -> None:
    attacker = resolve_container_name(env("ATT_OUT", "ATTACKER", default="attacker"))
    if attacker not in docker_container_names():
        log(f"attacker container {attacker!r} not found; skipping attacker cleanup")
        return
    run(["docker", "exec", attacker, "sh", "-lc", script], timeout=120)


def stop_attacker_pid_files(*relative_files: str) -> None:
    if not relative_files:
        return
    quoted = " ".join(shlex.quote(path) for path in relative_files)
    docker_exec_attacker_shell(
        'data="${DATA_PATH:-/tmp/KCData}"; '
        f"for rel in {quoted}; do "
        'bash /opt/attacker-lib/common/remove-pids.sh "$data/$rel" || true; '
        "done"
    )


def kind_node_container_names() -> list[str]:
    lab_name = env("LAB_NAME", "CLUSTER_PROFILE", default="")
    names = running_docker_container_names()
    if not lab_name:
        return []
    return sorted(
        name
        for name in names
        if name.startswith(f"{lab_name}-") and ("control-plane" in name or "worker" in name)
    )


def flush_kind_neighbour_cache() -> None:
    names = kind_node_container_names()
    for name in names:
        run(["docker", "exec", name, "sh", "-lc", "ip neigh flush all >/dev/null 2>&1 || true"], timeout=30, log_fail=False)
    if names:
        log(f"flushed neighbour cache on {len(names)} kind node containers")


def refresh_cluster_networking() -> None:
    restarted = False
    for daemonset in ("cilium", "kube-proxy"):
        if kubectl("-n", "kube-system", "rollout", "restart", f"daemonset/{daemonset}", timeout=60, log_fail=False):
            restarted = True
            kubectl(
                "-n",
                "kube-system",
                "rollout",
                "status",
                f"daemonset/{daemonset}",
                "--timeout=180s",
                timeout=240,
                log_fail=False,
            )
    if restarted:
        log("refreshed cluster networking agents after MITM cleanup")


def cleanup_attacker_network(kc_dir: str, *, mitm: bool = False) -> None:
    mitm_enabled = "1" if mitm else "0"
    docker_exec_attacker_shell(
        f"""
set +e
data="${{DATA_PATH:-/tmp/KCData}}"
kc={shlex.quote(kc_dir)}
for rel in "$kc/pids" "$kc/arp_pids"; do
  bash /opt/attacker-lib/common/remove-pids.sh "$data/$rel" || true
done
pkill -f "$data/$kc/node_traffic" || true
pkill -f "$data/$kc/tcpdump" || true
if [ {shlex.quote(mitm_enabled)} = 1 ]; then
  cp_host="${{CONTROL_PLANE_NODE:-kind-control-plane}}"
  cp_port="${{CONTROL_PLANE_PORT:-6443}}"
  cp_ip="$(getent hosts "$cp_host" | awk '{{print $1; exit}}')"
  service_ip="${{KUBERNETES_SERVICE_HOST:-${{KUBERNETES_SERVICE_IP:-10.96.0.1}}}}"
  service_port="${{KUBERNETES_SERVICE_PORT:-443}}"
  service_listen="${{MITM_SERVICE_LISTEN_PORT:-8443}}"
  if [ -n "$cp_ip" ]; then
    while iptables -t nat -D PREROUTING -i eth0 -p tcp -d "$cp_ip" --dport "$cp_port" -j REDIRECT --to-ports "$cp_port" 2>/dev/null; do :; done
    pkill -f "arpspoof .*${{cp_ip}}" || true
  fi
  while iptables -t nat -D PREROUTING -i eth0 -p tcp -d "$service_ip" --dport "$service_port" -j REDIRECT --to-ports "$service_listen" 2>/dev/null; do :; done
  pkill -f "sslsplit .*${{cp_port}}" || true
fi
ip neigh flush all >/dev/null 2>&1 || true
""".strip()
    )
    flush_kind_neighbour_cache()


def generic_restore(key: str) -> None:
    for selector in (f"honeypot.attack.kc={key}", f"honeypot.killchain/id={key}"):
        kubectl("delete", "deploy,svc,job,pod,daemonset,cronjob", "-A", "-l", selector, "--ignore-not-found=true")
        kubectl("delete", "role,rolebinding,serviceaccount,configmap,secret", "-A", "-l", selector, "--ignore-not-found=true")
        kubectl("delete", "clusterrole,clusterrolebinding", "-l", selector, "--ignore-not-found=true")
        kubectl("delete", "networkpolicy", "-A", "-l", selector, "--ignore-not-found=true")


def restart_free5gc_workloads() -> None:
    namespace = env("CORE_NAMESPACE", "FREE5GC_NAMESPACE", default="free5gc")
    kubectl("-n", namespace, "rollout", "restart", "deployment", log_fail=False)
    kubectl("-n", namespace, "rollout", "restart", "statefulset", log_fail=False)


def restore_kc1() -> None:
    docker_exec_attacker_shell("python3 /opt/attacker-lib/nwdaf/unregister_nrf.py || true")
    restart_free5gc_workloads()
    log("removed deterministic test NF registrations and restarted free5GC workloads")


def restore_kc2() -> None:
    namespace = env("CORE_NAMESPACE", "FREE5GC_NAMESPACE", default="free5gc")
    kubectl("-n", namespace, "rollout", "undo", "statefulset/mongodb-nwdaf", log_fail=False)
    kubectl("-n", namespace, "rollout", "restart", "statefulset/mongodb-nwdaf", log_fail=False)
    restart_free5gc_workloads()
    log("restored mongodb-nwdaf rollout and restarted free5GC workloads")


def restore_kc3() -> None:
    host_ns = env("PRIV_DEPLOY_NAMESPACE", "UPDATER_NAMESPACE", "LOG_NS", "MEM_NAMESPACE", default="mem")
    cleanup_attacker_network("KC5", mitm=True)
    refresh_cluster_networking()
    kubectl("-n", host_ns, "delete", "deployment", "-l", "app=ultra-priv", "--ignore-not-found=true")
    for selector in ("honeypot.killchain/id=KC5", "honeypot.killchain/id=KC3"):
        kubectl("delete", "cronjob,job,pod", "-A", "-l", selector, "--ignore-not-found=true")
    restart_free5gc_workloads()
    log("removed privileged node controllers, PVC wipers and stale KC3/KC5 processes")


def restore_kc4() -> None:
    for name in ("unauthenticated-admin", "ops-admin-crb"):
        kubectl("delete", "clusterrolebinding", name, "--ignore-not-found=true")
    kubectl("-n", "kube-system", "delete", "serviceaccount", "ops-admin", "--ignore-not-found=true")
    kubectl("-n", "kube-system", "delete", "daemonset", "node-agent", "--ignore-not-found=true")
    kubectl("delete", "namespace", env("MINING_NS", default="mining"), "--ignore-not-found=true")
    for container in ("k8s-apiserver-ephem", "kc6-apiserver"):
        run(["docker", "rm", "-f", container], timeout=30, log_fail=False)
    restart_free5gc_workloads()
    log("removed KC4 admin RBAC, DaemonSet and resource-hijacking workload")


RESTORE_BY_KEY = {
    "KC1": restore_kc1,
    "KC2": restore_kc2,
    "KC3": restore_kc3,
    "KC4": restore_kc4,
}


def main() -> None:
    key = canonical_kc_key(str(globals().get("KILLCHAIN_KEY") or "KC"))
    if key == "KC0":
        log("restore skipped for KC0")
        return
    done_flag = f"HONEYPOT_RESTORE_DONE_{key}"
    if os.environ.get(done_flag) == "1":
        log(f"restore already completed for {key}; skipping duplicate entrypoint")
        return

    log(f"restore started for {key}")
    generic_restore(key)
    restore = RESTORE_BY_KEY.get(key)
    if restore:
        restore()
    else:
        restart_free5gc_workloads()
        log(f"no specific restore registered for {key}; restarted free5GC workloads")
    os.environ[done_flag] = "1"
    log(f"restore completed for {key}")


if __name__ == "__main__":
    main()
