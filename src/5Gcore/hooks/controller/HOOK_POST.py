#!/usr/bin/env python3
"""Collect 5Gcore Caldera artifacts after each adversary completes."""

from __future__ import annotations

import os
import subprocess


KC_DATA_MAP = {
    "KC2": "KC2",
    "KC3": "KC5",
    "KC4": "KC6",
}


def log(*parts: object) -> None:
    print("[5gcore-controller-hook]", *parts, flush=True)


def run_capture(cmd: list[str], *, timeout: int = 60) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return proc.returncode, proc.stdout or "", proc.stderr or ""
    except Exception as exc:
        return 1, "", repr(exc)


def docker_names() -> set[str]:
    rc, out, _ = run_capture(["docker", "ps", "-a", "--format", "{{.Names}}"])
    return {line.strip() for line in out.splitlines()} if rc == 0 else set()


def resolve_container_name(base: str) -> str:
    names = docker_names()
    lab_name = os.getenv("LAB_NAME", "").strip()
    candidates = [base]
    if lab_name and not base.endswith(f"-{lab_name}"):
        candidates.append(f"{base}-{lab_name}")
    for candidate in candidates:
        if candidate in names:
            return candidate
    return base


def copy_tar_stream(producer_cmd: list[str], destination: str, *, timeout: int = 180) -> bool:
    os.makedirs(destination, exist_ok=True)
    producer = subprocess.Popen(
        producer_cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if producer.stdout is None:
        producer.kill()
        log("copy failed: producer stdout unavailable")
        return False

    consumer = subprocess.Popen(
        ["tar", "-C", destination, "-xf", "-"],
        stdin=producer.stdout,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    producer.stdout.close()

    try:
        out_bytes, err_bytes = consumer.communicate(timeout=timeout)
        producer_rc = producer.wait(timeout=5)
        producer_err = producer.stderr.read()
    except subprocess.TimeoutExpired:
        producer.kill()
        consumer.kill()
        log(f"copy timed out after {timeout}s")
        return False

    error = (producer_err + err_bytes).decode("utf-8", "replace") if producer_err or err_bytes else ""
    output = out_bytes.decode("utf-8", "replace") if out_bytes else ""
    rc = producer_rc if producer_rc != 0 else consumer.returncode
    if rc != 0 and "file changed as we read it" in error and os.listdir(destination):
        return True
    if rc != 0:
        log(f"copy failed: {(error or output).strip()}")
        return False
    return True


def copy_docker_dir_contents(container: str, source: str, destination: str) -> bool:
    rc, _, _ = run_capture(["docker", "exec", container, "test", "-d", source])
    if rc != 0:
        log(f"source not found: {container}:{source}")
        return False

    ok = copy_tar_stream(["docker", "exec", container, "tar", "-C", source, "-cf", "-", "."] , destination)
    if ok:
        log(f"copied {container}:{source} -> {destination}")
    return ok


def kubectl_base() -> list[str]:
    cmd = ["kubectl"]
    context = os.getenv("KUBE_CONTEXT", "").strip()
    if context:
        cmd.extend(["--context", context])
    return cmd


def copy_pod_dir_contents(namespace: str, pod: str, source: str, destination: str) -> bool:
    rc, _, _ = run_capture([*kubectl_base(), "-n", namespace, "exec", pod, "--", "test", "-d", source])
    if rc != 0:
        log(f"source not found: {namespace}/{pod}:{source}")
        return False

    producer = [
        *kubectl_base(),
        "-n",
        namespace,
        "exec",
        pod,
        "--",
        "tar",
        "-C",
        source,
        "-cf",
        "-",
        ".",
    ]
    ok = copy_tar_stream(producer, destination)
    if ok:
        log(f"copied {namespace}/{pod}:{source} -> {destination}")
    return ok


def discover_nwdaf_pod(namespace: str) -> str:
    selectors = [
        "app.kubernetes.io/name=free5gc-nwdaf",
        "nf=nwdaf",
    ]
    for selector in selectors:
        rc, out, _ = run_capture(
            [
                *kubectl_base(),
                "-n",
                namespace,
                "get",
                "pod",
                "-l",
                selector,
                "-o",
                "jsonpath={.items[0].metadata.name}",
            ],
            timeout=20,
        )
        if rc == 0 and out.strip():
            return out.strip()
    return ""


def copy_kc1_results() -> None:
    namespace = os.getenv("CORE_NAMESPACE", os.getenv("FREE5GC_NAMESPACE", "free5gc"))
    pod = os.getenv("ATT_IN", "").strip() or discover_nwdaf_pod(namespace)
    if not pod:
        log("NWDAF pod not found; skipping KC1 copy")
        return
    copy_pod_dir_contents(namespace, pod, "/tmp/KCData/KC1", "/results/KC1")


def copy_underlay_results(kc_key: str) -> None:
    source_key = KC_DATA_MAP.get(kc_key)
    if not source_key:
        return

    attacker = resolve_container_name(os.getenv("ATT_OUT", os.getenv("ATTACKER", "attacker")))
    if attacker not in docker_names():
        log(f"attacker container {attacker!r} not found; skipping {kc_key} copy")
        return

    data_path = "/tmp/KCData"
    rc, out, _ = run_capture(["docker", "exec", attacker, "printenv", "DATA_PATH"], timeout=10)
    if rc == 0 and out.strip():
        data_path = out.strip()

    copy_docker_dir_contents(attacker, f"{data_path}/{source_key}", f"/results/{kc_key}")


def copy_caldera_event_logs() -> None:
    caldera = resolve_container_name(os.getenv("CALDERA_SERVER", "caldera"))
    if caldera not in docker_names():
        log(f"Caldera container {caldera!r} not found; skipping event logs")
        return
    rc, _, _ = run_capture(["docker", "exec", caldera, "test", "-d", "/tmp/event_logs"])
    if rc != 0:
        log(f"Caldera event logs not found in {caldera}")
        return
    copy_docker_dir_contents(caldera, "/tmp/event_logs", "/results/caldera")


def collect_k8s_events() -> None:
    out_dir = "/results/kube_events"
    os.makedirs(out_dir, exist_ok=True)
    rc, out, err = run_capture([*kubectl_base(), "get", "events", "-A", "-o", "json"], timeout=120)
    if rc == 0:
        with open(os.path.join(out_dir, "kubernetes_events_raw.json"), "w", encoding="utf-8") as handle:
            handle.write(out)
        log(f"wrote Kubernetes events to {out_dir}/kubernetes_events_raw.json")
    else:
        log(f"Kubernetes events export failed: {(err or out).strip()}")


def main() -> None:
    kc_key = str(globals().get("KILLCHAIN_KEY") or os.getenv("KILLCHAIN_KEY", ""))
    if not kc_key:
        return

    if kc_key == "KC1":
        copy_kc1_results()
    else:
        copy_underlay_results(kc_key)

    if kc_key == "KC4":
        copy_caldera_event_logs()
        collect_k8s_events()


if __name__ == "__main__":
    main()
