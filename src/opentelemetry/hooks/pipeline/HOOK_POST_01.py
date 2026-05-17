#!/usr/bin/env python3
"""Discover OpenTelemetry cluster details after Kind creation.

After the generic Kind step creates or reuses the cluster, this hook discovers
node/container addresses, prepares host mappings, configures API-server access for
attack scenarios, and records details needed by later Compose and Skaffold hooks."""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path
from typing import Any

from lib import (
    config_bool,
    config_int,
    config_str,
    die,
    docker_bind_source,
    docker_first_container_ip,
    find_docker_container_by_ip,
    kind_cluster_name,
    kubectl,
    kube_context_name,
    log,
    node_internal_ip,
    run_cmd,
    set_state_value,
    warn,
)


def is_ipv4_literal(value: str) -> bool:
    parts = value.split(".")
    return len(parts) == 4 and all(part.isdigit() and 0 <= int(part) <= 255 for part in parts)


def is_ipv6_literal(value: str) -> bool:
    return ":" in value


def is_loopback_ip(value: str) -> bool:
    return value in {"127.0.0.1", "::1"}


def normalize_ip_entries(value: str, *, source_name: str) -> list[str]:
    """Normalize comma-separated IP/list input into unique host IPs."""
    stripped = value.strip()
    if stripped in {"", "auto", "[]"}:
        return []

    stripped = stripped.removeprefix("[").removesuffix("]")
    entries: list[str] = []
    seen: set[str] = set()

    for raw in stripped.split(","):
        entry = raw.strip().strip("\"'")
        if not entry:
            continue

        if "/" in entry:
            ip, prefix = entry.rsplit("/", 1)
            if prefix not in {"32", "128"}:
                die(f"{source_name} supports only host CIDRs (/32 or /128), got: {entry}")
            entry = ip

        if not is_ipv4_literal(entry) and not is_ipv6_literal(entry):
            die(f"Invalid API server IP in {source_name}: {entry}")
        if is_loopback_ip(entry) or entry in seen:
            continue

        seen.add(entry)
        entries.append(entry)

    return entries


def skaffold_ip_list_value(value: str) -> str:
    entries = normalize_ip_entries(value, source_name="KUBE_APISERVER_IPS")
    if not entries:
        return ""
    return ",".join(entries).replace(",", r"\\,")


def discover_kube_apiserver_ips(control_plane_ip: str) -> None:
    configured = config_str(CONFIG, "KUBE_APISERVER_IPS", "auto").strip()
    if configured not in {"", "auto", "[]"}:
        ips = normalize_ip_entries(configured, source_name="KUBE_APISERVER_IPS")
        set_state_value(STATE, "kube_apiserver_ips", ips)
        set_state_value(STATE, "KUBE_APISERVER_IPS", skaffold_ip_list_value(configured))
        return

    legacy_cidrs = config_str(CONFIG, "KUBE_APISERVER_CIDRS", "").strip()
    if legacy_cidrs not in {"", "auto", "[]"}:
        ips = normalize_ip_entries(legacy_cidrs, source_name="KUBE_APISERVER_CIDRS")
        CONFIG["KUBE_APISERVER_IPS"] = ",".join(ips)
        set_state_value(STATE, "kube_apiserver_ips", ips)
        set_state_value(STATE, "KUBE_APISERVER_IPS", skaffold_ip_list_value(CONFIG["KUBE_APISERVER_IPS"]))
        log(f"Using API server IPs from KUBE_APISERVER_CIDRS: {CONFIG['KUBE_APISERVER_IPS'] or '(none)'}")
        return

    ips: list[str] = []
    endpoint_ips = kubectl(
        CONFIG,
        [
            "get",
            "endpoints",
            "kubernetes",
            "-n",
            "default",
            "-o",
            "jsonpath={range .subsets[*].addresses[*]}{.ip}{\"\\n\"}{end}",
        ],
        check=False,
        capture_output=True,
        quiet=True,
    ).stdout.splitlines()
    ips.extend(ip.strip() for ip in endpoint_ips if ip.strip())

    service_ip = kubectl(
        CONFIG,
        ["get", "svc", "kubernetes", "-n", "default", "-o", "jsonpath={.spec.clusterIP}"],
        check=False,
        capture_output=True,
        quiet=True,
    ).stdout.strip()
    if service_ip and service_ip.lower() != "none":
        ips.append(service_ip)

    service_ips = kubectl(
        CONFIG,
        [
            "get",
            "svc",
            "kubernetes",
            "-n",
            "default",
            "-o",
            "jsonpath={range .spec.clusterIPs[*]}{.}{\"\\n\"}{end}",
        ],
        check=False,
        capture_output=True,
        quiet=True,
    ).stdout.splitlines()
    ips.extend(ip.strip() for ip in service_ips if ip.strip() and ip.strip().lower() != "none")

    if control_plane_ip:
        ips.append(control_plane_ip)
    else:
        control_plane = f"{kind_cluster_name(CONFIG)}-control-plane"
        docker_ip = docker_first_container_ip(control_plane, CONFIG)
        if docker_ip:
            ips.append(docker_ip)

    deduped = []
    for ip in ips:
        if (is_ipv4_literal(ip) or is_ipv6_literal(ip)) and not is_loopback_ip(ip) and ip not in deduped:
            deduped.append(ip)
    CONFIG["KUBE_APISERVER_IPS"] = ",".join(deduped)
    set_state_value(STATE, "kube_apiserver_ips", deduped)
    set_state_value(STATE, "KUBE_APISERVER_IPS", skaffold_ip_list_value(CONFIG["KUBE_APISERVER_IPS"]))
    log(f"Discovered kube-apiserver IPs: {CONFIG['KUBE_APISERVER_IPS'] or '(none)'}")


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
        if (
            "node-role.kubernetes.io/control-plane" in labels
            or "node-role.kubernetes.io/master" in labels
        ):
            control_planes.append(name)

    if len(all_nodes) < 3:
        die(f"Total nodes less than 3 (found {len(all_nodes)}). Aborting.")
    if not control_planes:
        die("No control-plane/master node found by Kubernetes labels.")

    workers = [node for node in all_nodes if node not in set(control_planes)]
    if len(workers) < 2:
        die(f"Need at least 2 worker nodes but found {len(workers)}.")

    log(f"Control-plane node(s): {' '.join(control_planes)}")
    log(f"Worker nodes: {' '.join(workers)}")
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
        die(
            f"Could not find a Docker container with IPv4 {cp_ip}. "
            "Is this a kind/minikube Docker cluster?"
        )

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


def docker_network_ipv4_subnet(network: str) -> str:
    completed = run_cmd(
        ["docker", "network", "inspect", network, "--format", "{{json .IPAM.Config}}"],
        check=False,
        capture_output=True,
        config=CONFIG,
    )
    if completed.returncode != 0 or not completed.stdout.strip():
        die(f"docker network inspect returned nothing for network {network}. Cannot compute IP.")

    try:
        configs = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        die(f"Could not parse Docker network IPAM for {network}: {exc}")

    for entry in configs:
        subnet = str(entry.get("Subnet", "")).strip()
        if "/" in subnet and ":" not in subnet:
            return subnet
    die(f"No IPv4 subnet found on network {network}. Enable IPv4 on the Docker network.")


def configure_metallb_addresses(network: str) -> tuple[str, str, str]:
    subnet = docker_network_ipv4_subnet(network)
    base_ip = subnet.split("/", 1)[0]
    octets = base_ip.split(".")
    if len(octets) != 4:
        die(f"Unexpected IPv4 subnet base: {base_ip}")

    frontend_proxy_ip = ".".join([*octets[:3], "200"])
    generic_svc_addr = ".".join([*octets[:3], "201"])

    CONFIG["FRONTEND_PROXY_IP"] = frontend_proxy_ip
    CONFIG["GENERIC_SVC_ADDR"] = generic_svc_addr
    set_state_value(STATE, "FRONTEND_PROXY_IP", frontend_proxy_ip)
    set_state_value(STATE, "GENERIC_SVC_ADDR", generic_svc_addr)
    set_state_value(STATE, "docker_network_ipv4_subnet", subnet)
    log(
        f"Detected IPv4 subnet for {network}: {subnet}; "
        f"frontend-proxy={frontend_proxy_ip}, generic={generic_svc_addr}"
    )
    return subnet, frontend_proxy_ip, generic_svc_addr


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

    iphost_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
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

    if not config_bool(CONFIG, "API_CERT", False):
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


def enable_ro_port_on_worker(node: str) -> None:
    context = kube_context_name(CONFIG)
    ip = node_internal_ip(node, kube_context=context, config=CONFIG)
    if not ip:
        warn(f"Could not get IPv4 InternalIP for {node}; skipping kubelet read-only port.")
        return

    pair = find_docker_container_by_ip(ip, CONFIG)
    if not pair:
        warn(f"No Docker container found for node {node} (IP {ip}); skipping.")
        return

    name, _, _ = split_container_pair(pair)
    log(f"Patching kubelet on worker {node} (container: {name}, IP: {ip}).")
    script = r'''
set -euo pipefail
cfg="/var/lib/kubelet/config.yaml"
dropin_dir="/etc/systemd/system/kubelet.service.d"
dropin="$dropin_dir/20-roport.conf"

already_enabled() {
  if command -v curl >/dev/null 2>&1; then
    code="$(curl -sS -o /dev/null -m 2 -w "%{http_code}" http://127.0.0.1:10255/healthz || true)"
    if [[ "$code" == "200" || "$code" == "401" || "$code" == "403" ]]; then
      return 0
    fi
  fi
  if command -v ss >/dev/null 2>&1; then
    ss -ltn "( sport = :10255 )" 2>/dev/null | grep -q LISTEN && return 0
  elif command -v netstat >/dev/null 2>&1; then
    netstat -lnt 2>/dev/null | awk '{print $4}' | grep -q ":10255$" && return 0
  fi
  return 1
}

if already_enabled; then
  echo "kubelet read-only port (10255) is already enabled."
  exit 0
fi

if [[ -f "$cfg" ]]; then
  if grep -qE "^[[:space:]]*address:" "$cfg"; then
    sed -i -E "s|^[[:space:]]*address:.*$|address: \"0.0.0.0\"|" "$cfg"
  else
    printf "\naddress: \"0.0.0.0\"\n" >> "$cfg"
  fi

  if grep -qE "^[[:space:]]*readOnlyPort:" "$cfg"; then
    sed -i -E "s|^[[:space:]]*readOnlyPort:.*$|readOnlyPort: 10255|" "$cfg"
  else
    printf "readOnlyPort: 10255\n" >> "$cfg"
  fi

  systemctl daemon-reload || true
  systemctl restart kubelet
else
  mkdir -p "$dropin_dir"
  cat > "$dropin" <<EOF
[Service]
Environment="KUBELET_EXTRA_ARGS=--address=0.0.0.0 --read-only-port=10255"
EOF
  systemctl daemon-reload
  systemctl restart kubelet
fi

sleep 2
curl -sS -o /dev/null -w "kubelet 10255 on localhost: %{http_code}\n" http://127.0.0.1:10255/healthz || true
'''
    completed = run_cmd(
        ["docker", "exec", "-i", name, "bash", "-lc", script],
        check=False,
        config=CONFIG,
    )
    if completed.returncode != 0:
        warn(f"Failed to patch kubelet on {node} (container {name}).")


def configure_kubelet_read_only_ports(workers: list[str]) -> None:
    if not config_bool(CONFIG, "OPEN_PORTS", False):
        set_state_value(STATE, "kubelet_readonly_ports_enabled", False)
        return

    for node in workers:
        enable_ro_port_on_worker(node)
    set_state_value(STATE, "kubelet_readonly_ports_enabled", True)


def patch_etcd_exposure(container: str) -> None:
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


def apiserver_identity() -> tuple[str, str]:
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
            "jsonpath={.items[0].metadata.name}|{.items[0].metadata.uid}",
        ],
        check=False,
        capture_output=True,
        quiet=True,
    )
    pod, _, uid = completed.stdout.partition("|")
    return pod.strip(), uid.strip()


def wait_api_reachable_short() -> bool:
    deadline = time.monotonic() + 10
    while time.monotonic() <= deadline:
        if kubectl(CONFIG, ["version", "--request-timeout=5s"], check=False, quiet=True).returncode == 0:
            return True
        time.sleep(1)
    return False


def wait_new_apiserver_ready(previous_uid: str, container: str) -> None:
    timeout = config_int(CONFIG, "APISERVER_WAIT_TIMEOUT", 300, minimum=1)
    deadline = time.monotonic() + timeout
    last_pod = ""

    while time.monotonic() <= deadline:
        wait_api_reachable_short()
        pod, uid = apiserver_identity()
        last_pod = pod or last_pod

        if pod and uid and uid != previous_uid:
            log(f"Detected new kube-apiserver pod: {pod}. Waiting for Ready.")
            ready = kubectl(
                CONFIG,
                [
                    "-n",
                    "kube-system",
                    "wait",
                    "--for=condition=Ready",
                    f"pod/{pod}",
                    "--timeout=180s",
                ],
                check=False,
                quiet=True,
            )
            if ready.returncode == 0:
                log(f"kube-apiserver is Ready: {pod}")
                return
            warn("New kube-apiserver pod did not become Ready within 180s; retrying.")

        time.sleep(2)

    kubectl(CONFIG, ["-n", "kube-system", "get", "pod", "-o", "wide"], check=False)
    if last_pod:
        kubectl(CONFIG, ["-n", "kube-system", "describe", "pod", last_pod], check=False)
    run_cmd(["docker", "logs", "--tail", "200", container], check=False, config=CONFIG)
    die(f"Timeout ({timeout}s) waiting for kube-apiserver to be recreated and Ready.")


def patch_anonymous_auth(container: str) -> None:
    if not config_bool(CONFIG, "ANONYMOUS_AUTH", False):
        set_state_value(STATE, "anonymous_auth_manifest_patched", False)
        return

    _prev_pod, prev_uid = apiserver_identity()
    script = r'''
set -euo pipefail
mf="/etc/kubernetes/manifests/kube-apiserver.yaml"

if grep -q -- "--anonymous-auth=true" "$mf"; then
  echo "kube-apiserver already accepts anonymous requests."
  exit 0
fi

if grep -q -- '--anonymous-auth=' "$mf"; then
  sed -i -E 's#--anonymous-auth=(true|false)#--anonymous-auth=true#' "$mf"
else
  sed -i -E '/^[[:space:]]*- --authorization-mode=/a\    - --anonymous-auth=true' "$mf" || \
  sed -i -E '/^[[:space:]]*- --kubelet-client-certificate=/a\    - --anonymous-auth=true' "$mf"
fi
echo "Manifest updated: --anonymous-auth=true added/enforced."
'''
    completed = run_cmd(
        ["docker", "exec", "-i", container, "bash", "-lc", script],
        check=False,
        capture_output=True,
        config=CONFIG,
    )
    print(completed.stdout, end="")
    if completed.returncode != 0:
        if completed.stderr:
            print(completed.stderr, end="")
        die(f"Failed to patch kube-apiserver manifest inside {container}.")

    if "already accepts anonymous requests" not in completed.stdout:
        wait_new_apiserver_ready(prev_uid, container)
    set_state_value(STATE, "anonymous_auth_manifest_patched", True)


def update_runtime_template_values(crictl_runtime_path: str) -> None:
    CONFIG["CRICTL_RUNTIME_PATH"] = crictl_runtime_path
    if config_bool(CONFIG, "SOCKET_SHARED", True):
        CONFIG["SMTP_SOCKET_PATH"] = crictl_runtime_path
        CONFIG["SMTP_SOCKET_TYPE"] = "Socket"
    else:
        CONFIG["SMTP_SOCKET_PATH"] = "/tmp/disabled-containerd.sock"
        CONFIG["SMTP_SOCKET_TYPE"] = "FileOrCreate"

    set_state_value(STATE, "CRICTL_RUNTIME_PATH", crictl_runtime_path)
    set_state_value(STATE, "SMTP_SOCKET_PATH", CONFIG["SMTP_SOCKET_PATH"])
    set_state_value(STATE, "SMTP_SOCKET_TYPE", CONFIG["SMTP_SOCKET_TYPE"])


def main() -> None:
    all_nodes, control_planes, workers = discover_nodes()
    control_plane_node = control_planes[0]
    cp_ip, cp_container, cp_networks, cp_network = resolve_control_plane(control_plane_node)
    crictl_runtime_path = read_crictl_runtime_path(cp_container)

    CONFIG["CONTROL_PLANE_NODE"] = control_plane_node
    kubeserver_port = discover_kubeserver_port()
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
    configure_metallb_addresses(cp_network)
    write_iphost_file(control_planes, workers)
    prepare_apiserver_dir(cp_container)
    update_runtime_template_values(crictl_runtime_path)
    configure_kubelet_read_only_ports(workers)
    patch_etcd_exposure(cp_container)
    patch_anonymous_auth(cp_container)
    discover_kube_apiserver_ips(cp_ip)


if __name__ == "__main__":
    main()
