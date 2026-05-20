#!/usr/bin/env python3
"""Best-effort soft restore for OpenTelemetry kill chains."""

from __future__ import annotations

import os
import subprocess


def log(*parts: object) -> None:
    print("[otel-restore]", *parts, flush=True)


def run(command: list[str]) -> None:
    completed = subprocess.run(command, text=True, check=False)
    if completed.returncode != 0:
        log("command failed", " ".join(command), "exit", completed.returncode)


def kubectl(*args: str) -> None:
    context = os.getenv("KUBE_CONTEXT", "")
    command = ["kubectl"]
    if context:
        command.extend(["--context", context])
    command.extend(args)
    run(command)


def docker_exec_attacker(*args: str) -> None:
    lab_name = os.getenv("LAB_NAME", os.getenv("CLUSTER_PROFILE", "honeypotlab"))
    attacker = f"{os.getenv('ATTACKER', 'attacker')}-{lab_name}"
    run(["docker", "exec", attacker, *args])


def main() -> None:
    key = globals().get("KILLCHAIN_KEY") or "KC"
    log(f"soft restore started for {key}")
    docker_exec_attacker("bash", "/opt/attacker-lib/common/remove-pids.sh")
    kubectl("delete", "deploy,svc,job,pod,daemonset", "-A", "-l", f"honeypot.attack.kc={key}", "--ignore-not-found=true")
    kubectl("delete", "clusterrole,clusterrolebinding", "-l", f"honeypot.attack.kc={key}", "--ignore-not-found=true")
    kubectl("delete", "networkpolicy", "-A", "-l", f"honeypot.attack.kc={key}", "--ignore-not-found=true")
    kubectl("-n", "kube-system", "rollout", "restart", "deployment/coredns")
    log(f"soft restore completed for {key}")


if __name__ == "__main__":
    main()
