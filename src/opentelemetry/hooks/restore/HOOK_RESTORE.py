#!/usr/bin/env python3
"""Best-effort restore for OpenTelemetry kill chains.

The restore is intentionally narrow: remove the resources or runtime mutations
introduced by the executed kill chain, then restart only the workloads that need
to reload clean state.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import urllib.error
import urllib.request


def log(*parts: object) -> None:
    print("[otel-restore]", *parts, flush=True)


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


def kubectl_json(*args: str) -> dict:
    completed = run(kubectl_cmd(*args, "-o", "json"), timeout=60, log_fail=False)
    if completed.returncode != 0 or not completed.stdout.strip():
        return {}
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError:
        return {}


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
    run(["docker", "exec", attacker, "sh", "-lc", script], timeout=90)


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


def registry_request(url: str, method: str, headers: dict[str, str] | None = None) -> tuple[int, dict[str, str]]:
    request = urllib.request.Request(url, method=method, headers=headers or {})
    user = os.getenv("REGISTRY_USER", "")
    password = os.getenv("REGISTRY_PASS", "")
    if user:
        import base64

        token = f"{user}:{password}".encode("utf-8")
        request.add_header("Authorization", "Basic " + base64.b64encode(token).decode("ascii"))
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, dict(response.headers.items())
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers.items())
    except Exception as exc:  # noqa: BLE001 - registry cleanup is best effort.
        log(f"registry request failed for {url}: {exc!r}")
        return 0, {}


def delete_registry_tag(image_name: str, image_tag: str) -> None:
    registry_host = f"{env('REGISTRY_NAME', default='registry')}:{env('REGISTRY_PORT', default='5000')}"
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
        log(f"registry tag cleanup {image_name}:{image_tag} -> HTTP {delete_status}")
    else:
        log(f"registry tag {image_name}:{image_tag} not present")


def generic_restore(key: str) -> None:
    for selector in (f"honeypot.attack.kc={key}", f"honeypot.killchain/id={key}"):
        kubectl("delete", "deploy,svc,job,pod,daemonset,cronjob", "-A", "-l", selector, "--ignore-not-found=true")
        kubectl("delete", "role,rolebinding,serviceaccount,configmap,secret", "-A", "-l", selector, "--ignore-not-found=true")
        kubectl("delete", "clusterrole,clusterrolebinding", "-l", selector, "--ignore-not-found=true")
        kubectl("delete", "networkpolicy", "-A", "-l", selector, "--ignore-not-found=true")


def remove_deployment_containers(namespace: str, deployment: str, names: set[str]) -> None:
    data = kubectl_json("-n", namespace, "get", "deployment", deployment)
    containers = data.get("spec", {}).get("template", {}).get("spec", {}).get("containers", [])
    patches = [
        {"op": "remove", "path": f"/spec/template/spec/containers/{index}"}
        for index, container in reversed(list(enumerate(containers)))
        if container.get("name") in names
    ]
    if patches:
        kubectl("-n", namespace, "patch", "deployment", deployment, "--type=json", "-p", json.dumps(patches))
        log(f"removed injected containers from {namespace}/{deployment}")


def remove_service_ports(namespace: str, service: str, port_names: set[str]) -> None:
    data = kubectl_json("-n", namespace, "get", "service", service)
    ports = data.get("spec", {}).get("ports", [])
    patches = [
        {"op": "remove", "path": f"/spec/ports/{index}"}
        for index, port in reversed(list(enumerate(ports)))
        if port.get("name") in port_names or str(port.get("port")) == "8080"
    ]
    if patches:
        kubectl("-n", namespace, "patch", "service", service, "--type=json", "-p", json.dumps(patches))
        log(f"removed injected service ports from {namespace}/{service}")


def remove_coredns_rewrite() -> None:
    auth_ns = env("AUTH_NS", "AUTH_NAMESPACE", "AUT_NAMESPACE", default="auth")
    attacked_ns = env("DMZ_NAMESPACE", "ATTACKED_NS", default="dmz")
    expected = f"rewrite name auth.{auth_ns}.svc.cluster.local image-provider.{attacked_ns}.svc.cluster.local"
    data = kubectl_json("-n", "kube-system", "get", "configmap", "coredns")
    corefile = data.get("data", {}).get("Corefile", "")
    if not corefile:
        return
    lines = [line for line in corefile.splitlines() if line.strip() != expected]
    new_corefile = "\n".join(lines) + ("\n" if corefile.endswith("\n") else "")
    if new_corefile != corefile:
        patch = json.dumps({"data": {"Corefile": new_corefile}})
        kubectl("-n", "kube-system", "patch", "configmap", "coredns", "--type=merge", "-p", patch)
        log("removed CoreDNS rewrite for image-provider redirection")


def restore_network_policies_from_helm() -> None:
    charts_root = env("HELM_CHARTS_ROOT", default="")
    chart = os.path.join(charts_root, "additions") if charts_root else ""
    if not chart or not os.path.isdir(chart):
        log("HELM_CHARTS_ROOT/additions not available; NetworkPolicy chart replay skipped")
        return
    if run(["helm", "version", "--short"], timeout=20, log_fail=False).returncode != 0:
        return
    run(
        [
            "helm",
            "upgrade",
            "honeypot-additions",
            chart,
            "--namespace",
            "default",
            "--reuse-values",
            "--wait",
            "--timeout",
            env("HELM_TIMEOUT", default="10m"),
        ],
        timeout=900,
    )
    log("replayed honeypot-additions release for NetworkPolicy restoration")


def restore_kc1() -> None:
    dmz_ns = env("DMZ_NAMESPACE", default="dmz")
    remove_deployment_containers(
        dmz_ns,
        "image-provider",
        {"sidecar-not-malicious", "sidecar-not-mining", "sidecar-not-mining2"},
    )
    remove_service_ports(dmz_ns, "image-provider", {"http-8080"})
    remove_coredns_rewrite()
    kubectl("-n", "kube-system", "rollout", "restart", "deployment/coredns")
    kubectl("-n", dmz_ns, "rollout", "restart", "deployment/image-provider")


def restore_kc2() -> None:
    app_ns = env("APP_NAMESPACE", "NSPROTO", default="app")
    cleanup_attacker_network("KC2")
    delete_registry_tag("checkout", env("CHECKOUT_IMAGE_TAG", default="2.0.3"))
    kubectl("-n", app_ns, "rollout", "restart", "deployment/checkout")


def restore_kc3() -> None:
    app_ns = env("APP_NAMESPACE", "NSPROTO", default="app")
    dat_ns = env("DAT_NAMESPACE", "NSDATA", default="dat")
    mem_ns = env("MEM_NAMESPACE", "NSCREDS", default="mem")
    kubectl("-n", app_ns, "delete", "job", "-l", "app.kubernetes.io/name=insert-currency-rate", "--ignore-not-found=true")
    sql = (
        'PGPASSWORD="${POSTGRES_PASSWORD:-}" '
        'psql -U "${POSTGRES_USER:-postgres}" -d "${POSTGRES_DB:-currency}" '
        "-v ON_ERROR_STOP=1 -c \"DELETE FROM currency WHERE code = 'NUL';\""
    )
    kubectl("-n", dat_ns, "exec", "statefulset/postgres", "--", "sh", "-lc", sql)
    kubectl("-n", app_ns, "rollout", "restart", "deployment/currency")
    kubectl("-n", mem_ns, "rollout", "restart", "deployment/flagd")
    log("removed injected NUL currency and restarted currency/flagd")


def restore_kc4() -> None:
    dmz_ns = env("DMZ_NAMESPACE", default="dmz")
    kubectl("-n", dmz_ns, "rollout", "restart", "deployment/smtp")
    restore_network_policies_from_helm()


def restore_kc5() -> None:
    host_ns = env("PRIV_DEPLOY_NAMESPACE", "UPDATER_NAMESPACE", "LOG_NS", "MEM_NAMESPACE", default="mem")
    cleanup_attacker_network("KC5", mitm=True)
    refresh_cluster_networking()
    kubectl("-n", host_ns, "delete", "deployment", "-l", "app=ultra-priv", "--ignore-not-found=true")
    kubectl("delete", "cronjob,job,pod", "-A", "-l", "honeypot.killchain/id=KC5", "--ignore-not-found=true")
    for namespace in {
        env("APP_NAMESPACE", default="app"),
        env("DMZ_NAMESPACE", default="dmz"),
        env("MEM_NAMESPACE", default="mem"),
        env("PAY_NAMESPACE", default="pay"),
    }:
        kubectl("-n", namespace, "rollout", "restart", "deployment")


def restore_kc6() -> None:
    dmz_ns = env("DMZ_NAMESPACE", default="dmz")
    for name in ("unauthenticated-admin", "ops-admin-crb"):
        kubectl("delete", "clusterrolebinding", name, "--ignore-not-found=true")
    kubectl("-n", "kube-system", "delete", "serviceaccount", "ops-admin", "--ignore-not-found=true")
    kubectl("-n", "kube-system", "delete", "daemonset", "node-agent", "--ignore-not-found=true")
    for container in ("k8s-apiserver-ephem", "kc6-apiserver"):
        run(["docker", "rm", "-f", container], timeout=30, log_fail=False)
    delete_registry_tag("frontend-proxy", env("FRONTEND_PROXY_IMAGE_TAG", "IMAGE_VERSION", default="2.0.2"))
    kubectl("-n", dmz_ns, "rollout", "restart", "deployment/frontend-proxy")


RESTORE_BY_KEY = {
    "KC1": restore_kc1,
    "KC2": restore_kc2,
    "KC3": restore_kc3,
    "KC4": restore_kc4,
    "KC5": restore_kc5,
    "KC6": restore_kc6,
}


def main() -> None:
    key = canonical_kc_key(str(globals().get("KILLCHAIN_KEY") or "KC"))
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
        log(f"no specific restore registered for {key}")
    os.environ[done_flag] = "1"
    log(f"restore completed for {key}")


if __name__ == "__main__":
    main()
