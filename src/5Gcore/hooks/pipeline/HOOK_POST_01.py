#!/usr/bin/env python3
"""Discover 5Gcore Kind details after cluster creation.

This prepares the node inventory, control-plane endpoint, API-server cert
scratch directory, and containerd socket path consumed later by Compose,
Skaffold, and the Caldera kill-chain scripts."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from lib import (
    config_bool,
    config_int,
    config_str,
    die,
    docker_bind_source,
    find_docker_container_by_ip,
    kubectl,
    kube_context_name,
    log,
    node_internal_ip,
    run_cmd,
    set_state_value,
    warn,
)


def kubectl_json(args: list[str]) -> dict[str, Any]:
    completed = kubectl(CONFIG, args, capture_output=True)
    return json.loads(completed.stdout)


def discover_nodes() -> tuple[list[str], list[str], list[str]]:
    data = kubectl_json(["get", "nodes", "-o", "json"])
    all_nodes: list[str] = []
    control_planes: list[str] = []

    for item in data.get("items", []):
        metadata = item.get("metadata", {})
        name = str(metadata.get("name", "")).strip()
        if not name:
            continue

        all_nodes.append(name)
        labels = metadata.get("labels", {}) or {}
        if "node-role.kubernetes.io/control-plane" in labels or "node-role.kubernetes.io/master" in labels:
            control_planes.append(name)

    if not all_nodes:
        die("No Kubernetes nodes found.")
    if not control_planes:
        die("No control-plane/master node found by Kubernetes labels.")

    workers = [node for node in all_nodes if node not in set(control_planes)]
    if not workers:
        warn("No worker nodes found; host-network KC2 service may land on the control plane.")

    log(f"Control-plane node(s): {' '.join(control_planes)}")
    log(f"Worker nodes: {' '.join(workers) if workers else '(none)'}")
    return all_nodes, control_planes, workers


def split_container_pair(pair: str) -> tuple[str, str, str]:
    name, _, networks = pair.partition(";")
    primary_network = networks.split()[0] if networks.split() else ""
    return name, networks, primary_network


def resolve_control_plane(control_plane_node: str) -> tuple[str, str, str, str]:
    context = kube_context_name(CONFIG)
    cp_ip = node_internal_ip(control_plane_node, kube_context=context, config=CONFIG)
    if not cp_ip:
        die(f"Could not determine IPv4 InternalIP of control-plane node {control_plane_node}.")

    pair = find_docker_container_by_ip(cp_ip, CONFIG)
    if not pair:
        die(f"Could not find a Docker container with IPv4 {cp_ip}.")

    container, networks, primary_network = split_container_pair(pair)
    if not primary_network:
        die(f"Could not determine Docker network for control-plane container {container}.")

    log(f"Chosen control-plane node: {control_plane_node} with IPv4 InternalIP {cp_ip}")
    log(f"Control-plane container: {container}")
    log(f"Control-plane docker network(s): {networks}")
    log(f"Primary docker network chosen: {primary_network}")
    return cp_ip, container, networks, primary_network


def read_crictl_runtime_path(container: str) -> str:
    completed = run_cmd(
        [
            "docker",
            "exec",
            "-i",
            container,
            "sh",
            "-lc",
            "sed -n 's/^runtime-endpoint:[[:space:]]*//p' /etc/crictl.yaml 2>/dev/null | head -n1",
        ],
        check=False,
        capture_output=True,
        config=CONFIG,
    )
    path = completed.stdout.strip().removeprefix("unix://")
    if not path:
        warn(f"Could not read runtime-endpoint from /etc/crictl.yaml in {container}.")
        path = "/run/containerd/containerd.sock"
    else:
        log(f"Control-plane CRI runtime socket path: {path}")
    return path


def discover_kubeserver_port() -> str:
    completed = kubectl(
        CONFIG,
        [
            "-n",
            "default",
            "get",
            "endpoints",
            "kubernetes",
            "-o",
            "jsonpath={.subsets[0].ports[0].port}",
        ],
        check=False,
        capture_output=True,
        quiet=True,
    )
    port = completed.stdout.strip() if completed.returncode == 0 else ""
    return port or "6443"


def discover_kube_apiserver_image() -> str:
    completed = kubectl(
        CONFIG,
        [
            "-n",
            "kube-system",
            "get",
            "pod",
            "-l",
            "component=kube-apiserver",
            "-o",
            "jsonpath={.items[0].spec.containers[0].image}",
        ],
        check=False,
        capture_output=True,
        quiet=True,
    )
    image = completed.stdout.strip() if completed.returncode == 0 else ""
    return image or "registry.k8s.io/kube-apiserver:v1.30.0"


def taint_control_plane(control_plane_node: str) -> None:
    log("Tainting control-plane node to exclude application pods.")
    kubectl(
        CONFIG,
        [
            "taint",
            "nodes",
            control_plane_node,
            "node-role.kubernetes.io/control-plane=:NoSchedule",
            "--overwrite=true",
        ],
    )


def attacker_runtime_dir() -> Path:
    path = Path(config_str(CONFIG, "ATTACKER_RUNTIME_DIR", "/res/runtime/attacker"))
    path.mkdir(parents=True, exist_ok=True)
    CONFIG["ATTACKER_RUNTIME_DIR"] = str(path)
    return path


def write_iphost_file(control_planes: list[str], workers: list[str]) -> Path:
    context = kube_context_name(CONFIG)
    attacker_dir = attacker_runtime_dir()
    iphost_file = attacker_dir / "iphost"
    if iphost_file.is_dir():
        warn(f"Removing directory at {iphost_file}; attacker iphost must be a file.")
        shutil.rmtree(iphost_file)

    lines: list[str] = []
    for idx, node in enumerate(control_planes):
        ip = node_internal_ip(node, kube_context=context, config=CONFIG)
        if not ip:
            warn(f"No IPv4 InternalIP for control-plane node {node}; skipping iphost entry.")
            continue
        label = "control-plane" if idx == 0 else f"control-plane{idx + 1}"
        lines.append(f"{ip} - {label}")

    for idx, node in enumerate(sorted(workers), start=1):
        ip = node_internal_ip(node, kube_context=context, config=CONFIG)
        if not ip:
            warn(f"No IPv4 InternalIP for worker node {node}; skipping iphost entry.")
            continue
        lines.append(f"{ip} - worker{idx}")

    iphost_file.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    CONFIG["COMPOSE_ATTACKER_IPHOST_FILE"] = str(docker_bind_source(iphost_file, CONFIG))
    set_state_value(STATE, "iphost_file", str(iphost_file))
    set_state_value(STATE, "COMPOSE_ATTACKER_IPHOST_FILE", CONFIG["COMPOSE_ATTACKER_IPHOST_FILE"])
    log(f"Wrote iphost file at {iphost_file}")
    return iphost_file


def prepare_apiserver_dir(container: str) -> Path:
    apiserver_dir = attacker_runtime_dir() / "apiserver"
    apiserver_dir.mkdir(parents=True, exist_ok=True)
    CONFIG["COMPOSE_ATTACKER_APISERVER_DIR"] = str(docker_bind_source(apiserver_dir, CONFIG))
    set_state_value(STATE, "COMPOSE_ATTACKER_APISERVER_DIR", CONFIG["COMPOSE_ATTACKER_APISERVER_DIR"])

    if not config_bool(CONFIG, "API_CERT", True):
        set_state_value(STATE, "apiserver_certs_copied", False)
        return apiserver_dir

    candidates = [
        ("/etc/kubernetes/pki/apiserver.crt", "/etc/kubernetes/pki/apiserver.key"),
        ("/var/lib/minikube/certs/apiserver.crt", "/var/lib/minikube/certs/apiserver.key"),
    ]
    for cert_path, key_path in candidates:
        cert = run_cmd(
            ["docker", "cp", f"{container}:{cert_path}", str(apiserver_dir / "apiserver.crt")],
            check=False,
            quiet=True,
            config=CONFIG,
        )
        key = run_cmd(
            ["docker", "cp", f"{container}:{key_path}", str(apiserver_dir / "apiserver.key")],
            check=False,
            quiet=True,
            config=CONFIG,
        )
        if cert.returncode == 0 and key.returncode == 0:
            log(f"Copied apiserver certificates to {apiserver_dir}")
            set_state_value(STATE, "apiserver_certs_copied", True)
            return apiserver_dir

    warn(f"Failed to copy apiserver certs from {container}.")
    set_state_value(STATE, "apiserver_certs_copied", False)
    return apiserver_dir


def update_runtime_template_values(crictl_runtime_path: str) -> None:
    CONFIG["CRICTL_RUNTIME_PATH"] = crictl_runtime_path
    if config_bool(CONFIG, "SOCKET_SHARED", True):
        socket_path = crictl_runtime_path
        socket_type = "Socket"
    else:
        socket_path = "/tmp/disabled-containerd.sock"
        socket_type = "FileOrCreate"

    CONFIG["PCF_SOCKET_PATH"] = socket_path
    CONFIG["PCF_SOCKET_TYPE"] = socket_type
    CONFIG["SMTP_SOCKET_PATH"] = socket_path
    CONFIG["SMTP_SOCKET_TYPE"] = socket_type

    set_state_value(STATE, "CRICTL_RUNTIME_PATH", crictl_runtime_path)
    set_state_value(STATE, "PCF_SOCKET_PATH", socket_path)
    set_state_value(STATE, "PCF_SOCKET_TYPE", socket_type)


def patch_etcd_exposure(container: str) -> None:
    """Expose the unauthenticated etcd HTTP endpoint used by KC4."""
    if not config_bool(CONFIG, "ETCD_EXPOSURE", False):
        set_state_value(STATE, "etcd_manifest_patched", False)
        return

    port = config_int(CONFIG, "PLAIN_PORT", 12379, minimum=1, maximum=65535)
    script = f'''
set -euo pipefail
mf="/etc/kubernetes/manifests/etcd.yaml"
plain_port="{port}"

port_open() {{
  if command -v curl >/dev/null 2>&1; then
    code="$(curl -sS -m 2 -o /dev/null -w "%{{http_code}}" "http://127.0.0.1:${{plain_port}}/health" || true)"
    [[ "$code" =~ ^(200|204|301|302|400|401|403|404|405)$ ]] && return 0
  fi
  if command -v ss >/dev/null 2>&1; then
    ss -ltn "( sport = :${{plain_port}} )" 2>/dev/null | grep -q LISTEN && return 0
  elif command -v netstat >/dev/null 2>&1; then
    netstat -lnt 2>/dev/null | awk '{{print $4}}' | grep -q ":${{plain_port}}$" && return 0
  fi
  return 1
}}

if port_open; then
  echo "etcd client port $plain_port already open."
  exit 0
fi

if grep -q -- '--listen-client-urls=' "$mf"; then
  sed -i -E "/--listen-client-urls=/ s#(--listen-client-urls=).*#\\1https://127.0.0.1:2379,http://0.0.0.0:${{plain_port}}#" "$mf"
else
  sed -i -E "/^[[:space:]]*- --data-dir=/a\\    - --listen-client-urls=https:\\/\\/127.0.0.1:2379,http:\\/\\/0.0.0.0:${{plain_port}}" "$mf"
fi
'''
    completed = run_cmd(
        ["docker", "exec", "-i", container, "bash", "-lc", script],
        check=False,
        config=CONFIG,
    )
    if completed.returncode != 0:
        die(f"Failed to patch etcd manifest inside {container}.")
    set_state_value(STATE, "etcd_manifest_patched", True)


def main() -> None:
    all_nodes, control_planes, workers = discover_nodes()
    control_plane_node = control_planes[0]
    cp_ip, cp_container, cp_networks, cp_network = resolve_control_plane(control_plane_node)
    crictl_runtime_path = read_crictl_runtime_path(cp_container)
    kubeserver_port = discover_kubeserver_port()

    CONFIG["CONTROL_PLANE_NODE"] = control_plane_node
    CONFIG["KUBESERVER_PORT"] = kubeserver_port
    CONFIG["CONTROL_PLANE_PORT"] = kubeserver_port
    CONFIG["K8S_IMAGE"] = discover_kube_apiserver_image()
    CONFIG["CP_CONTAINER"] = cp_container
    CONFIG["CP_NETWORK"] = cp_network
    CONFIG["CP_NETWORKS"] = cp_networks

    set_state_value(STATE, "all_nodes", all_nodes)
    set_state_value(STATE, "control_plane_nodes", control_planes)
    set_state_value(STATE, "worker_nodes", workers)
    set_state_value(STATE, "CONTROL_PLANE_NODE", control_plane_node)
    set_state_value(STATE, "CONTROL_PLANE_PORT", CONFIG["CONTROL_PLANE_PORT"])
    set_state_value(STATE, "KUBESERVER_PORT", CONFIG["KUBESERVER_PORT"])
    set_state_value(STATE, "K8S_IMAGE", CONFIG["K8S_IMAGE"])
    set_state_value(STATE, "control_plane_ip", cp_ip)
    set_state_value(STATE, "control_plane_container", cp_container)
    set_state_value(STATE, "CP_NETWORK", cp_network)

    taint_control_plane(control_plane_node)
    patch_etcd_exposure(cp_container)
    write_iphost_file(control_planes, workers)
    prepare_apiserver_dir(cp_container)
    update_runtime_template_values(crictl_runtime_path)


if __name__ == "__main__":
    main()
