#!/usr/bin/env python3
"""Collect OpenTelemetry artifacts after each Caldera adversary completes.

The generic controller calls this hook with operation/adversary context. The hook
collects Kubernetes events, pod information, Caldera event logs, and related data
from the OpenTelemetry target so each kill-chain run leaves useful evidence in
the results directory."""

from __future__ import annotations

import base64
import os
import subprocess
import urllib.error
import urllib.request


def log(*parts: object) -> None:
    print("[otel-controller-hook]", *parts, flush=True)


def run_capture(cmd: list[str], *, timeout: int = 60) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return proc.returncode, proc.stdout or "", proc.stderr or ""
    except Exception as exc:
        return 1, "", repr(exc)


def copy_docker_tree(container: str, source: str, destination: str, *, timeout: int = 120) -> tuple[int, str, str]:
    source_clean = source.rstrip("/")
    parent = os.path.dirname(source_clean) or "/"
    leaf = os.path.basename(source_clean)
    os.makedirs(destination, exist_ok=True)

    producer = subprocess.Popen(
        ["docker", "exec", container, "tar", "-C", parent, "-cf", "-", leaf],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if producer.stdout is None:
        producer.kill()
        return 1, "", "docker tar stdout unavailable"

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
        return 1, "", f"copy timeout after {timeout}s"

    output = out_bytes.decode("utf-8", "replace") if out_bytes else ""
    error = (producer_err + err_bytes).decode("utf-8", "replace") if producer_err or err_bytes else ""
    rc = producer_rc if producer_rc != 0 else consumer.returncode
    if rc != 0 and "file changed as we read it" in error:
        copied_leaf = os.path.join(destination, leaf)
        if os.path.exists(copied_leaf):
            return 0, output, error
    return rc, output, error


def docker_container_exists(name: str) -> bool:
    rc, out, _ = run_capture(["docker", "ps", "-a", "--format", "{{.Names}}"])
    return rc == 0 and name in {line.strip() for line in out.splitlines()}


def resolve_container_name(base: str) -> str:
    rc, out, _ = run_capture(["docker", "ps", "-a", "--format", "{{.Names}}"])
    if rc != 0:
        return base

    names = {line.strip() for line in out.splitlines()}
    lab_name = os.getenv("LAB_NAME", "").strip()
    candidates = [base]
    if lab_name and not base.endswith(f"-{lab_name}"):
        candidates.append(f"{base}-{lab_name}")

    for candidate in candidates:
        if candidate in names:
            return candidate
    return base


def docker_exec_stdout(container: str, args: list[str], *, timeout: int = 60) -> str:
    rc, out, err = run_capture(["docker", "exec", container, *args], timeout=timeout)
    if rc != 0:
        log(f"docker exec failed in {container}: {(err or out).strip()}")
        return ""
    return out.strip()


def copy_docker_kc_results(kc_key: str) -> None:
    attacker = resolve_container_name(os.getenv("ATT_OUT", os.getenv("ATTACKER", "attacker")))
    if not docker_container_exists(attacker):
        log(f"attacker container {attacker!r} not found; skipping {kc_key} docker results")
        return

    data_path = docker_exec_stdout(attacker, ["printenv", "DATA_PATH"]) or "/tmp/KCData"
    rc, _, _ = run_capture(["docker", "exec", attacker, "test", "-d", f"{data_path}/{kc_key}"])
    if rc != 0:
        log(f"no {kc_key} results in {attacker}:{data_path}; skipping copy")
        return

    os.makedirs("/results", exist_ok=True)
    rc, out, err = copy_docker_tree(attacker, f"{data_path}/{kc_key}", "/results", timeout=120)
    if rc == 0:
        log(f"copied {kc_key} results from {attacker}:{data_path}/{kc_key}")
    else:
        log(f"failed copying {kc_key} results: {(err or out).strip()}")


def copy_kc1_pod_results() -> None:
    pod = os.getenv("ATT_IN", "")
    namespace = os.getenv("ATT_NS") or os.getenv("TST_NAMESPACE") or "tst"
    if not pod:
        pod = discover_attacker_pod(namespace)
    if not pod:
        log("ATT_IN not found; skipping KC1 pod results")
        return

    rc, out, err = run_capture(["kubectl", "-n", namespace, "exec", pod, "--", "printenv", "DATA_PATH"])
    if rc != 0:
        log(f"failed reading DATA_PATH from pod {pod}: {(err or out).strip()}")
        return

    data_path = out.strip() or "/tmp/KCData"
    os.makedirs("/results", exist_ok=True)
    rc, out, err = run_capture(["kubectl", "-n", namespace, "cp", f"{pod}:{data_path}/KC1", "/results/"], timeout=120)
    if rc == 0:
        log(f"copied KC1 pod results from {pod}:{data_path}/KC1")
    else:
        log(f"failed copying KC1 pod results: {(err or out).strip()}")


def discover_attacker_pod(namespace: str) -> str:
    for selector in ("app.kubernetes.io/name=test-image", "app=test-image"):
        rc, out, _ = run_capture(
            [
                "kubectl",
                "-n",
                namespace,
                "get",
                "pod",
                "-l",
                selector,
                "-o",
                "jsonpath={.items[0].metadata.name}",
            ],
            timeout=10,
        )
        if rc == 0 and out.strip():
            os.environ["ATT_IN"] = out.strip()
            return out.strip()

    rc, out, _ = run_capture(["kubectl", "-n", namespace, "get", "pods", "--no-headers"], timeout=10)
    if rc != 0:
        return ""
    for line in out.splitlines():
        columns = line.split()
        if columns and columns[0].startswith("test-image-"):
            os.environ["ATT_IN"] = columns[0]
            return columns[0]
    return ""


def copy_caldera_event_logs() -> None:
    caldera = resolve_container_name(os.getenv("CALDERA_SERVER", "caldera"))
    if not docker_container_exists(caldera):
        log(f"Caldera container {caldera!r} not found; skipping event logs")
        return

    rc, _, _ = run_capture(["docker", "exec", caldera, "sh", "-lc", "test -d /tmp/event_logs"])
    if rc != 0:
        log(f"Caldera event logs not found in {caldera}")
        return

    os.makedirs("/results/caldera", exist_ok=True)
    rc, out, err = run_capture(["docker", "cp", f"{caldera}:/tmp/event_logs/.", "/results/caldera/"], timeout=120)
    if rc == 0:
        log(f"copied Caldera event logs from {caldera}")
    else:
        log(f"failed copying Caldera event logs: {(err or out).strip()}")


def collect_k8s_events() -> None:
    out_dir = os.getenv("KUBE_EVENTS_DIR", "/results/kube_events")
    os.makedirs(out_dir, exist_ok=True)
    raw_file = os.path.join(out_dir, "kubernetes_events_raw.json")
    cmd = ["kubectl"]
    kube_ctx = os.getenv("KUBE_CONTEXT", "")
    if kube_ctx:
        cmd.extend(["--context", kube_ctx])
    cmd.extend(["get", "events", "-A", "-o", "json"])
    rc, out, err = run_capture(cmd, timeout=120)
    if rc == 0:
        with open(raw_file, "w", encoding="utf-8") as handle:
            handle.write(out)
        log(f"wrote Kubernetes events to {raw_file}")
    else:
        log(f"Kubernetes events export failed: {(err or out).strip()}")


def registry_request(url: str, method: str, headers: dict[str, str] | None = None) -> tuple[int, dict[str, str]]:
    req = urllib.request.Request(url, method=method, headers=headers or {})
    user = os.getenv("REGISTRY_USER", "")
    password = os.getenv("REGISTRY_PASS", "")
    if user:
        token = f"{user}:{password}".encode("utf-8")
        req.add_header("Authorization", "Basic " + base64.b64encode(token).decode("ascii"))
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            return response.status, dict(response.headers.items())
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers.items())
    except Exception as exc:
        log(f"registry request failed for {url}: {exc!r}")
        return 0, {}


def delete_registry_tag(image_name: str, image_tag: str) -> None:
    registry_host = f"{os.getenv('REGISTRY_NAME', 'registry')}:{os.getenv('REGISTRY_PORT', '5000')}"
    accept = {"Accept": "application/vnd.docker.distribution.manifest.v2+json"}

    manifest_url = f"https://{registry_host}/v2/{image_name}/manifests/{image_tag}"
    status, headers = registry_request(manifest_url, "HEAD", accept)
    digest = ""
    for key, value in headers.items():
        if key.lower() == "docker-content-digest":
            digest = value
            break
    if status != 404 and digest:
        delete_url = f"https://{registry_host}/v2/{image_name}/manifests/{digest}"
        delete_status, _ = registry_request(delete_url, "DELETE")
        log(f"delete registry tag {image_name}:{image_tag} -> HTTP {delete_status}")
        return

    log(f"registry tag {image_name}:{image_tag} not found")


def main() -> None:
    kc_key = str(globals().get("KILLCHAIN_KEY") or "")
    if not kc_key:
        return

    if kc_key == "KC1":
        copy_kc1_pod_results()
    else:
        copy_docker_kc_results(kc_key)

    if kc_key == "KC6":
        copy_caldera_event_logs()
        collect_k8s_events()
        delete_registry_tag("checkout", os.getenv("CHECKOUT_IMAGE_TAG", "2.0.3"))
        delete_registry_tag("frontend-proxy", os.getenv("FRONTEND_PROXY_IMAGE_TAG", os.getenv("IMAGE_VERSION", "2.0.2")))


if __name__ == "__main__":
    main()
