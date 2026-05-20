#!/usr/bin/env python3
"""Finalize the OpenTelemetry deployment after Skaffold completes.

This hook performs target-specific post-deploy work: it labels namespaces, opens
or weakens intentionally vulnerable surfaces according to CONFIG, verifies service
reachability, and records smoke-test results."""

from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import time
from pathlib import Path
from typing import Any

from lib import (
    config_bool,
    config_str,
    die,
    docker_container_exists,
    find_docker_container_by_ip,
    get_state_value,
    kube_context_name,
    kubectl,
    kubectl_config_view_jsonpath,
    log,
    node_internal_ip,
    run_cmd,
    save_state_file,
    set_state_value,
    utc_timestamp,
    wait_http,
    warn,
)


def runtime_dir(*parts: str) -> Path:
    """Return a writable path below /res/runtime."""
    return Path(config_str(CONFIG, "RUNTIME_DIR", "/res/runtime")).joinpath(*parts)


def resolve_output_path(name: str, default: Path) -> Path:
    raw = config_str(CONFIG, name, "", allow_empty=True).strip()
    if not raw:
        return default

    path = Path(raw)
    if path.is_absolute():
        return path

    return Path(config_str(CONFIG, "PROJECT_ROOT", os.getcwd())).joinpath(path)


def namespaces() -> list[str]:
    """Return lab namespace names using legacy variable names."""
    names = []

    for name in (
        "APP_NAMESPACE",
        "DAT_NAMESPACE",
        "DMZ_NAMESPACE",
        "MEM_NAMESPACE",
        "PAY_NAMESPACE",
        "TST_NAMESPACE",
    ):
        namespace = config_str(CONFIG, name, "", allow_empty=True).strip()
        if namespace:
            names.append(namespace)
        else:
            log(f"Skipping namespace label: variable {name} is not set or empty.")

    return names


def ensure_controller_admin_rbac() -> None:
    """Ensure the legacy controller-admin ServiceAccount and CRB exist."""
    namespace = config_str(CONFIG, "SA_NAMESPACE", "kube-system", allow_empty=False)
    service_account = config_str(CONFIG, "SA_NAME", "controller-admin", allow_empty=False)
    cluster_role_binding = config_str(CONFIG, "CRB_NAME", "controller-admin", allow_empty=False)

    if kubectl(CONFIG, ["-n", namespace, "get", "sa", service_account], check=False, quiet=True).returncode != 0:
        kubectl(CONFIG, ["-n", namespace, "create", "sa", service_account])

    if kubectl(CONFIG, ["get", "clusterrolebinding", cluster_role_binding], check=False, quiet=True).returncode != 0:
        kubectl(
            CONFIG,
            [
                "create",
                "clusterrolebinding",
                cluster_role_binding,
                "--clusterrole=cluster-admin",
                f"--serviceaccount={namespace}:{service_account}",
            ],
        )

    set_state_value(
        STATE,
        "controller_admin_rbac",
        {
            "service_account_namespace": namespace,
            "service_account_name": service_account,
            "cluster_role_binding": cluster_role_binding,
        },
    )


def docker_running_container_names() -> set[str]:
    completed = run_cmd(
        ["docker", "ps", "--format", "{{.Names}}"],
        check=False,
        capture_output=True,
        config=CONFIG,
    )
    return {line.strip() for line in completed.stdout.splitlines() if line.strip()}


def lab_container_name(base: str) -> str:
    lab_name = config_str(CONFIG, "LAB_NAME", config_str(CONFIG, "CLUSTER_PROFILE", "honeypotlab"), allow_empty=False)
    suffix = f"-{lab_name}"
    if base.endswith(suffix):
        return base
    return f"{base}{suffix}"


def split_container_pair(pair: str) -> tuple[str, str]:
    name, _, networks = pair.partition(";")
    return name, networks.split()[0] if networks.split() else ""


def tcp_port_available(address: str, port: int) -> bool:
    """Return whether a TCP port can be bound in the current namespace."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind((address, port))
            return True
        except OSError:
            return False


def discover_api_server_host_and_network(context: str) -> tuple[str, str]:
    """Return a Docker-reachable API server host and its network."""
    running = docker_running_container_names()
    server_host = ""
    docker_network = ""

    if context.startswith("kind-"):
        cluster_name = context.removeprefix("kind-")
        expected = f"{cluster_name}-control-plane"

        if expected in running:
            server_host = expected
        elif cluster_name == "kind" and "kind-control-plane" in running:
            server_host = "kind-control-plane"
        else:
            warn(f"Could not find expected Kind control-plane container {expected!r}; trying IP discovery.")

        docker_network = config_str(CONFIG, "CP_NETWORK", "kind")

    elif context.startswith("k3d-"):
        cluster_name = context.removeprefix("k3d-")
        server_host = f"k3d-{cluster_name}-server-0"
        docker_network = f"k3d-{cluster_name}"

    elif context == "minikube" and "minikube" in running:
        server_host = "minikube"
        docker_network = "minikube"

    if not server_host:
        state_container = str(get_state_value(STATE, "control_plane_container", "") or "").strip()
        state_network = str(get_state_value(STATE, "CP_NETWORK", "") or "").strip()

        if state_container:
            server_host = state_container
            docker_network = docker_network or state_network

    if not server_host or not docker_network:
        control_plane_node = first_control_plane_node()
        if control_plane_node:
            ip = node_internal_ip(control_plane_node, kube_context=context, config=CONFIG)
            if ip:
                pair = find_docker_container_by_ip(ip, CONFIG)
                if pair:
                    server_host, network = split_container_pair(pair)
                    docker_network = docker_network or network

    return server_host, docker_network


def first_control_plane_node() -> str:
    for label in ("node-role.kubernetes.io/control-plane", "node-role.kubernetes.io/master"):
        completed = kubectl(
            CONFIG,
            [
                "get",
                "nodes",
                "-l",
                label,
                "-o",
                "jsonpath={range .items[*]}{.metadata.name}{\"\\n\"}{end}",
            ],
            check=False,
            capture_output=True,
            quiet=True,
        )
        nodes = [line.strip() for line in completed.stdout.splitlines() if line.strip()]

        if nodes:
            return nodes[0]

    return ""


def kubernetes_endpoint_port() -> str:
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
    return completed.stdout.strip() or "6443"


def service_account_token(namespace: str, service_account: str) -> str:
    completed = kubectl(
        CONFIG,
        ["-n", namespace, "create", "token", service_account],
        check=False,
        capture_output=True,
        quiet=True,
    )

    token = completed.stdout.strip()
    if token:
        return token

    warn("kubectl create token failed; trying legacy secret-based token retrieval.")

    secret = kubectl(
        CONFIG,
        ["-n", namespace, "get", "sa", service_account, "-o", "jsonpath={.secrets[0].name}"],
        check=False,
        capture_output=True,
        quiet=True,
    ).stdout.strip()

    if not secret:
        die("Failed to get token for ServiceAccount; no legacy secret found.")

    token = kubectl(
        CONFIG,
        ["-n", namespace, "get", "secret", secret, "-o", "go-template={{.data.token | base64decode}}"],
        check=False,
        capture_output=True,
        quiet=True,
    ).stdout.strip()

    if not token:
        die(f"Failed to obtain a token for ServiceAccount {namespace}/{service_account}.")

    return token


def write_controller_kubeconfig() -> Path:
    """Write the Docker-reachable controller-admin kubeconfig."""
    ensure_controller_admin_rbac()

    namespace = config_str(CONFIG, "SA_NAMESPACE", "kube-system", allow_empty=False)
    service_account = config_str(CONFIG, "SA_NAME", "controller-admin", allow_empty=False)
    context = kube_context_name(CONFIG)

    cluster_name = kubectl_config_view_jsonpath(
        context,
        "{.contexts[0].context.cluster}",
        config=CONFIG,
    )
    ca_data = kubectl_config_view_jsonpath(
        context,
        "{.clusters[0].cluster.certificate-authority-data}",
        config=CONFIG,
    )
    current_server = kubectl_config_view_jsonpath(
        context,
        "{.clusters[0].cluster.server}",
        config=CONFIG,
    )

    if not cluster_name:
        die("Unable to resolve cluster name from current context.")

    if not current_server:
        die("Unable to resolve current cluster server endpoint.")

    server_host, docker_network = discover_api_server_host_and_network(context)
    schema = current_server.split("://", 1)[0] if "://" in current_server else "https"
    server_url = f"{schema}://{server_host}:{kubernetes_endpoint_port()}" if server_host else current_server

    out_file = runtime_dir("controller", "kubeconfig")
    out_file.parent.mkdir(parents=True, exist_ok=True)

    token = service_account_token(namespace, service_account)
    tls_line = f"certificate-authority-data: {ca_data}" if ca_data else "insecure-skip-tls-verify: true"

    if not ca_data:
        warn("Cluster CA data not found. Falling back to insecure-skip-tls-verify.")

    content = "\n".join(
        [
            "apiVersion: v1",
            "kind: Config",
            "clusters:",
            "- cluster:",
            f"    server: {server_url}",
            f"    {tls_line}",
            f"  name: {cluster_name}",
            "contexts:",
            "- context:",
            f"    cluster: {cluster_name}",
            f"    user: {service_account}",
            f"  name: {cluster_name}",
            f"current-context: {cluster_name}",
            "users:",
            f"- name: {service_account}",
            "  user:",
            f"    token: {token}",
            "",
        ]
    )

    out_file.write_text(content, encoding="utf-8")

    local_kubeconfig = Path("/kube/kubeconfig")

    try:
        local_kubeconfig.parent.mkdir(parents=True, exist_ok=True)
        local_kubeconfig.write_text(content, encoding="utf-8")
    except OSError as exc:
        warn(f"Could not install local /kube/kubeconfig in the controller runtime: {exc}")

    log(f"Using API server endpoint: {server_url}")

    if docker_network:
        log(f"Docker network to use: {docker_network}")

    log(f"Kubeconfig ready: {out_file}")

    set_state_value(
        STATE,
        "controller_kubeconfig",
        {
            "path": str(out_file),
            "local_runtime_path": str(local_kubeconfig),
            "server": server_url,
            "cluster_name": cluster_name,
            "docker_network": docker_network,
        },
    )

    return out_file


def start_frontend_port_forward() -> None:
    """Start and supervise kubectl port-forward for the frontend."""
    if wait_http("http://127.0.0.1:8080", timeout_seconds=120):
        set_state_value(STATE, "frontend_port_forward_pid", None)
        set_state_value(STATE, "frontend_url", "http://localhost:8080")
        log("Frontend already reachable at http://127.0.0.1:8080; skipping kubectl port-forward.")
        return

    if not tcp_port_available("0.0.0.0", 8080):
        set_state_value(STATE, "frontend_port_forward_pid", None)
        set_state_value(STATE, "frontend_url", "http://localhost:8080")
        warn("Frontend port 8080 is already in use but not healthy; skipping kubectl port-forward.")
        return

    namespace = config_str(CONFIG, "DMZ_NAMESPACE", "dmz")
    log_file = Path(config_str(CONFIG, "RESULTS_DIR", "/results")) / "frontend-port-forward.log"
    log_file.parent.mkdir(parents=True, exist_ok=True)

    command = [
        "kubectl",
        "--context",
        kube_context_name(CONFIG),
        "-n",
        namespace,
        "port-forward",
        "--address",
        "0.0.0.0",
        "svc/frontend-proxy",
        "8080:8080",
    ]

    supervisor_script = runtime_dir("frontend-port-forward-supervisor.py")
    supervisor_script.parent.mkdir(parents=True, exist_ok=True)
    supervisor_script.write_text(
        """#!/usr/bin/env python3
import json
import os
import signal
import subprocess
import sys
import time
import urllib.request


def endpoint_ready(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=3) as response:
            return response.status < 500
    except Exception as exc:
        print(f"[port-forward] health check failed: {exc!r}", flush=True)
        return False


command = json.loads(os.environ["PORT_FORWARD_COMMAND"])
url = os.environ.get("PORT_FORWARD_HEALTH_URL", "http://127.0.0.1:8080")
interval = int(os.environ.get("PORT_FORWARD_CHECK_INTERVAL", "5"))
startup_grace = int(os.environ.get("PORT_FORWARD_STARTUP_GRACE", "30"))
failure_limit = int(os.environ.get("PORT_FORWARD_FAILURE_LIMIT", "3"))

while True:
    print("[port-forward] starting: " + " ".join(command), flush=True)
    process = subprocess.Popen(command, stdout=sys.stdout, stderr=subprocess.STDOUT, start_new_session=True)
    started = time.monotonic()
    failures = 0

    while True:
        code = process.poll()
        if code is not None:
            print(f"[port-forward] kubectl exited with code {code}; restarting in 2s", flush=True)
            break

        if endpoint_ready(url):
            failures = 0
        elif time.monotonic() - started >= startup_grace:
            failures += 1
            if failures >= failure_limit:
                print("[port-forward] unhealthy; restarting kubectl", flush=True)
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                    process.wait(timeout=5)
                except Exception:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except Exception:
                        pass
                break

        time.sleep(interval)

    time.sleep(2)
""",
        encoding="utf-8",
    )

    supervisor_env = os.environ.copy()
    supervisor_env["PORT_FORWARD_COMMAND"] = json.dumps(command)

    supervisor = ["python3", str(supervisor_script)]
    handle = log_file.open("ab", buffering=0)

    try:
        proc = subprocess.Popen(
            supervisor,
            stdout=handle,
            stderr=subprocess.STDOUT,
            env=supervisor_env,
            start_new_session=True,
        )
    finally:
        handle.close()

    time.sleep(3)

    if proc.poll() is not None:
        raise SystemExit(f"Frontend port-forward exited early; see {log_file}")

    set_state_value(STATE, "frontend_port_forward_pid", proc.pid)
    set_state_value(STATE, "frontend_url", "http://localhost:8080")

    log(f"Started frontend port-forward pid={proc.pid}.")


def configure_k8s_security() -> None:
    """Apply legacy Pod Security labels unless MISSING_POLICY=true."""
    if config_bool(CONFIG, "MISSING_POLICY", False):
        log("MISSING_POLICY=true; skipping Pod Security label application.")
        set_state_value(STATE, "k8s_security_configured", False)
        return

    labeled = []

    for namespace in namespaces():
        log(f"Labeling namespace: {namespace}")
        kubectl(
            CONFIG,
            [
                "label",
                "namespace",
                namespace,
                "pod-security.kubernetes.io/enforce=restricted",
                "pod-security.kubernetes.io/warn=restricted",
                "pod-security.kubernetes.io/audit=restricted",
                "--overwrite",
            ],
        )
        labeled.append(namespace)

    set_state_value(STATE, "k8s_security_configured", True)
    set_state_value(STATE, "pod_security_labeled_namespaces", labeled)


def coredns_configmap() -> dict[str, Any]:
    completed = kubectl(
        CONFIG,
        ["-n", "kube-system", "get", "configmap", "coredns", "-o", "json"],
        capture_output=True,
    )
    return json.loads(completed.stdout)


def apply_coredns_configmap(configmap: dict[str, Any]) -> None:
    kubectl(
        CONFIG,
        ["-n", "kube-system", "apply", "-f", "-"],
        input_text=json.dumps(configmap),
        quiet=True,
    )


def restart_coredns() -> None:
    kubectl(CONFIG, ["-n", "kube-system", "rollout", "restart", "deployment", "coredns"])


def remove_forward_directives(corefile: str) -> tuple[str, bool]:
    output: list[str] = []
    changed = False
    skipping = False
    depth = 0

    for line in corefile.splitlines():
        stripped = line.lstrip()

        if skipping:
            depth += line.count("{") - line.count("}")
            changed = True

            if depth <= 0:
                skipping = False

            continue

        if re.match(r"^forward\s+", stripped):
            changed = True

            if "{" in line:
                depth = line.count("{") - line.count("}")
                skipping = depth > 0

            continue

        output.append(line)

    return "\n".join(output) + "\n", changed


def remove_zone_block(corefile: str, zone: str) -> str:
    output: list[str] = []
    skipping = False
    depth = 0
    pattern = re.compile(rf"^{re.escape(zone)}(:[0-9]+)?\s*\{{")

    for line in corefile.splitlines():
        stripped = line.lstrip()

        if skipping:
            depth += line.count("{") - line.count("}")

            if depth <= 0:
                skipping = False

            continue

        if pattern.match(stripped):
            skipping = True
            depth = line.count("{") - line.count("}")

            if depth <= 0:
                skipping = False

            continue

        output.append(line)

    return "\n".join(output) + "\n"


def zone_block(zone: str, docker_ip: str) -> str:
    return "\n".join(
        [
            f"{zone}:53 {{",
            "    errors",
            "    cache 30",
            f"    forward . {docker_ip}",
            "    reload",
            "}",
        ]
    )


def insert_zone_block(corefile: str, zone: str, docker_ip: str) -> tuple[str, bool]:
    if re.search(rf"(?m)^\s*{re.escape(zone)}(:[0-9]+)?\s*\{{", corefile):
        log(f"Zone block for {zone!r} already present in Corefile. Nothing to do.")
        return corefile, False

    cleaned = remove_zone_block(corefile, zone)
    block = zone_block(zone, docker_ip)
    lines = cleaned.splitlines()
    output: list[str] = []
    inserted = False
    main_server = re.compile(r"^\s*(?:\.)?:53\s*\{")

    for line in lines:
        if not inserted and main_server.match(line):
            output.extend(block.splitlines())
            output.append("")
            inserted = True

        output.append(line)

    if not inserted:
        output = [*block.splitlines(), "", *output]

    return "\n".join(output) + "\n", True


def docker_container_ip(container_name: str) -> str:
    completed = run_cmd(
        ["docker", "inspect", "-f", "{{json .NetworkSettings.Networks}}", container_name],
        check=False,
        capture_output=True,
        config=CONFIG,
    )

    if completed.returncode != 0 or not completed.stdout.strip():
        return ""

    try:
        networks = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return ""

    preferred_network = config_str(CONFIG, "CP_NETWORK", "", allow_empty=True).strip()

    if preferred_network and preferred_network in networks:
        ip = str(networks[preferred_network].get("IPAddress", "")).strip()
        if ip:
            return ip

    for network_data in networks.values():
        ip = str(network_data.get("IPAddress", "")).strip()
        if ip:
            return ip

    return ""


def configure_coredns() -> None:
    """Apply the legacy RECURSIVE_DNS/CoreDNS behavior."""
    configmap = coredns_configmap()
    data = configmap.setdefault("data", {})
    corefile = str(data.get("Corefile", ""))

    if not corefile:
        die("Failed to read kube-system/coredns Corefile.")

    if not config_bool(CONFIG, "RECURSIVE_DNS", True):
        new_corefile, changed = remove_forward_directives(corefile)

        if changed:
            data["Corefile"] = new_corefile
            apply_coredns_configmap(configmap)
            restart_coredns()
            log("CoreDNS recursion disabled by removing forward directives.")
        else:
            log("No CoreDNS forward directive found; nothing to change.")

        set_state_value(STATE, "coredns_recursive_dns", False)
        return

    attacker = config_str(CONFIG, "ATTACKER", "attacker", allow_empty=False)
    attacker_container = lab_container_name(attacker)

    if not docker_container_exists(attacker_container, CONFIG):
        warn(f"Container {attacker_container!r} not found. Skipping attacker DNS zone insertion.")
        set_state_value(STATE, "coredns_attacker_zone_inserted", False)
        return

    docker_ip = docker_container_ip(attacker_container)

    if not docker_ip:
        die(f"Could not determine IP for docker container {attacker_container!r}.")

    new_corefile, changed = insert_zone_block(corefile, attacker, docker_ip)

    if changed:
        data["Corefile"] = new_corefile
        apply_coredns_configmap(configmap)
        restart_coredns()
        log(f"Inserted CoreDNS zone block for {attacker!r} forwarding to {docker_ip}.")

    set_state_value(
        STATE,
        "coredns_attacker_zone",
        {
            "zone": attacker,
            "container": attacker_container,
            "docker_ip": docker_ip,
            "inserted_or_present": True,
        },
    )


def run_smoke_tests() -> None:
    """Verify that the deployed lab is reachable."""
    checks: dict[str, Any] = {}

    checks["nodes"] = kubectl(CONFIG, ["get", "nodes"], capture_output=True).stdout
    checks["helm"] = run_cmd(
        ["helm", "--kube-context", kube_context_name(CONFIG), "list", "-A"],
        capture_output=True,
        config=CONFIG,
    ).stdout

    checks["frontend_http"] = wait_http("http://127.0.0.1:8080", timeout_seconds=120)

    caldera_url = config_str(CONFIG, "CALDERA_URL", "http://caldera:8888", allow_empty=False)
    checks["caldera_http"] = wait_http(caldera_url, timeout_seconds=10)

    set_state_value(STATE, "smoke_tests", checks)

    if not checks["frontend_http"]:
        raise SystemExit("Frontend is not reachable at http://127.0.0.1:8080.")

    if not checks["caldera_http"]:
        warn(f"Caldera is not reachable at {caldera_url}; kill-chain runner will report this clearly.")


def write_lab_state() -> None:
    """Persist the final lab state snapshot."""
    set_state_value(
        STATE,
        "lab_endpoints",
        {
            "frontend": "http://localhost:8080",
            "caldera": "http://localhost:8888",
            "results": config_str(CONFIG, "RESULTS_DIR", "/results"),
        },
    )
    set_state_value(STATE, "lab_state_written_at", utc_timestamp())
    save_state_file(STATE)
    log(f"Lab state written to {STATE.get('state_file')}")


def main() -> None:
    write_controller_kubeconfig()
    configure_k8s_security()
    configure_coredns()
    start_frontend_port_forward()
    run_smoke_tests()
    write_lab_state()


if __name__ == "__main__":
    main()
