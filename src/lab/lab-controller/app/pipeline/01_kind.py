#!/usr/bin/env python3
"""Render the target Kind configuration and create or reuse the Kind cluster."""

import json
import os
import re
import time
from pathlib import Path
from urllib.parse import urlsplit

from lib import (
    CommandError,
    config_bool,
    config_int,
    config_str,
    config_to_env,
    die,
    ensure_docker_network,
    kind_cluster_name,
    kind_clusters,
    kind_nodes,
    log,
    run_cmd,
    generated_dir,
    runtime_dir,
    set_state_value,
    substitute_vars,
    target_conf_file,
)


TEMPLATE_VAR_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


def kube_context_for_cluster(name: str) -> str:
    """Return the standard Kind kube context name for a cluster."""
    return f"kind-{name}"


def kind_template_path() -> Path:
    """Return the target Kind template path."""
    return target_conf_file(CONFIG, "kind-cluster.yaml.tmpl")


def kind_config_output_path(name: str) -> Path:
    """Return the rendered Kind config output path under res/runtime/generated."""
    out = generated_dir(CONFIG) / f"{name}.yaml"
    out.parent.mkdir(parents=True, exist_ok=True)
    return out


def template_variables(text: str) -> list[str]:
    """Return all template variable names referenced by the template."""
    return sorted({match.group(1) for match in TEMPLATE_VAR_RE.finditer(text)})


def missing_template_variables(text: str, values: dict[str, str]) -> list[str]:
    """Return required template variables missing from CONFIG-derived values.

    Variables with defaults, for example ${NAME:-default}, are not considered
    required.
    """
    required = {
        match.group(1)
        for match in TEMPLATE_VAR_RE.finditer(text)
        if match.group(2) is None
    }

    return sorted(
        name
        for name in required
        if name not in values or values[name] is None
    )


def render_kind_template(template: Path) -> str:
    """Render the Kind template using only variables already present in CONFIG."""
    template_text = template.read_text(encoding="utf-8")
    env = config_to_env(CONFIG, base_env={})

    missing = missing_template_variables(template_text, env)
    if missing:
        die(
            "Missing Kind template variables in CONFIG: "
            + ", ".join(missing)
            + f" (template: {template})"
        )

    set_state_value(STATE, "kind_template_variables", template_variables(template_text))

    return substitute_vars(template_text, env)


def write_kind_config(name: str) -> Path:
    """Render and write the Kind config file."""
    template = kind_template_path()

    if not template.is_file():
        die(f"Target Kind config template not found: {template}")

    out = kind_config_output_path(name)
    rendered = render_kind_template(template)

    if not rendered.endswith("\n"):
        rendered += "\n"

    out.write_text(rendered, encoding="utf-8")

    set_state_value(STATE, "kind_config_file", str(out))
    set_state_value(STATE, "kind_config_template_file", str(template))

    return out


def create_or_reuse_cluster(name: str, kind_config: Path) -> None:
    """Create the Kind cluster if missing, otherwise reuse it."""
    if name in kind_clusters(CONFIG):
        log(f"Reusing existing Kind cluster {name}.")
        try:
            export_cluster_kubeconfig(name)
            return
        except CommandError as exc:
            log(f"Existing Kind cluster {name} is not usable: {exc}")
            delete_cluster(name)

    create_cluster(name, kind_config)


def create_cluster(name: str, kind_config: Path) -> None:
    """Create a fresh Kind cluster."""
    network = config_str(CONFIG, "CP_NETWORK", f"kind-{name}", allow_empty=False)
    wait_duration = kind_create_wait_duration(CONFIG)
    env = config_to_env(CONFIG)
    env["KIND_EXPERIMENTAL_DOCKER_NETWORK"] = network
    cmd = [
        "kind",
        "create",
        "cluster",
        "--name",
        name,
        "--config",
        str(kind_config),
        "--wait",
        wait_duration,
    ]

    image = config_str(CONFIG, "K8S_IMAGE", "").strip()
    if image:
        cmd.extend(["--image", image])

    run_cmd(
        cmd,
        timeout_seconds=300,
        config=CONFIG,
        env=env,
    )

    log(f"Created Kind cluster {name} on Docker network {network} (kind wait={wait_duration}).")
    export_cluster_kubeconfig(name)


def kind_create_wait_duration(config: dict[str, object]) -> str:
    """Return the Kind create wait duration for the selected CNI mode."""
    explicit = config_str(config, "KIND_CREATE_WAIT", "", allow_empty=True).strip()
    if explicit:
        return explicit

    if str(config.get("KIND_DISABLE_DEFAULT_CNI_BLOCK", "")).strip():
        return "0s"

    if config_bool(config, "CILIUM_ENABLED", False):
        return "0s"

    return "180s"

def delete_cluster(name: str) -> None:
    """Delete an unusable local Kind cluster before recreating it."""
    completed = run_cmd(
        ["kind", "delete", "cluster", "--name", name],
        check=False,
        capture_output=True,
        timeout_seconds=300,
        config=CONFIG,
    )
    if completed.returncode != 0:
        message = (completed.stderr or completed.stdout or "").strip()
        die(f"Failed to delete unusable Kind cluster {name}: {message}")
    log(f"Deleted unusable Kind cluster {name}.")


def export_cluster_kubeconfig(name: str) -> None:
    """Ensure kubectl has a local context for the Kind cluster."""
    run_cmd(
        ["kind", "export", "kubeconfig", "--name", name],
        config=CONFIG,
        raise_on_error=True,
    )
    log(f"Exported kubeconfig for Kind cluster {name}.")


def kubeconfig_server(kubeconfig: Path, context: str) -> str:
    """Return the server URL from a kubeconfig context."""
    completed = run_cmd(
        [
            "kubectl",
            "--kubeconfig",
            str(kubeconfig),
            "config",
            "view",
            "--raw",
            "--minify",
            "--context",
            context,
            "-o",
            "jsonpath={.clusters[0].cluster.server}",
        ],
        check=False,
        capture_output=True,
        config=CONFIG,
    )
    return completed.stdout.strip() if completed.returncode == 0 else ""


def parse_server_host_port(server: str) -> tuple[str, int] | None:
    """Parse a Kubernetes API server URL into host and port."""
    try:
        parsed = urlsplit(server)
    except ValueError:
        return None
    if parsed.scheme != "https" or not parsed.hostname:
        return None
    return parsed.hostname, parsed.port or 443


def set_kubeconfig_server(kubeconfig: Path, context: str, server: str) -> None:
    """Set the cluster server URL while preserving the current context."""
    run_cmd(
        [
            "kubectl",
            "--kubeconfig",
            str(kubeconfig),
            "config",
            "set-cluster",
            context,
            "--server",
            server,
        ],
        quiet=True,
        config=CONFIG,
    )


def docker_published_port(container: str, container_port: str = "6443/tcp") -> str:
    """Return the host port published for a Docker container port."""
    completed = run_cmd(
        ["docker", "port", container, container_port],
        check=False,
        capture_output=True,
        quiet=True,
        config=CONFIG,
    )
    if completed.returncode != 0:
        return ""

    for raw_line in completed.stdout.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        return line.rsplit(":", 1)[-1].strip()
    return ""


def controller_proxy_host_port(timeout_seconds: int = 30) -> str:
    """Read the host-published controller proxy port from the runtime info file."""
    info_file = runtime_dir(CONFIG, "info")
    deadline = time.monotonic() + timeout_seconds

    while time.monotonic() < deadline:
        try:
            data = json.loads(info_file.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            time.sleep(1)
            continue

        proxy = data.get("controller_proxy", {})
        if isinstance(proxy, dict) and proxy.get("exposed"):
            host_port = proxy.get("host_port")
            if host_port:
                return str(host_port)
        time.sleep(1)

    return ""


def host_kube_api_server(name: str, original_server: str) -> tuple[str, str, dict[str, object]]:
    """Return the host-facing API server URL and proxy metadata."""
    host = config_str(CONFIG, "HOST_KUBE_API_SERVER_HOST", "127.0.0.1", allow_empty=False)
    host_socket = config_bool(CONFIG, "HOST_SOCKET", False)
    exposed = config_bool(CONFIG, "EXPOSE_TO_HOST", False)

    if host_socket:
        port = docker_published_port(f"{name}-control-plane")
        if port:
            return (
                f"https://{host}:{port}",
                "direct-kind-port",
                {"enabled": False, "reason": "HOST_SOCKET=true uses Kind's host-published API port"},
            )

        log("Could not discover Kind API host port; leaving host kubeconfig server unchanged.")
        return original_server, "kind-export-default", {"enabled": False, "reason": "kind port not found"}

    upstream = parse_server_host_port(original_server)
    if not exposed:
        log("EXPOSE_TO_HOST=false; host kubeconfig cannot be made reachable through the controller proxy.")
        return original_server, "not-exposed", {"enabled": False, "reason": "EXPOSE_TO_HOST=false"}

    proxy_port = controller_proxy_host_port()
    if not proxy_port:
        log("Could not discover controller proxy host port; leaving host kubeconfig server unchanged.")
        return original_server, "kind-export-default", {"enabled": False, "reason": "controller proxy port not found"}

    proxy_state: dict[str, object] = {"enabled": False}
    if upstream is not None:
        upstream_host, upstream_port = upstream
        proxy_state = {
            "enabled": True,
            "host": upstream_host,
            "port": upstream_port,
            "server": original_server,
        }
    else:
        log(f"Could not parse Kind API server for proxy target: {original_server!r}")

    return f"https://{host}:{proxy_port}", "controller-tls-proxy", proxy_state


def make_host_user_readable(path: Path) -> dict[str, object]:
    """Make the generated kubeconfig readable by the host user that owns RUNTIME_DIR."""
    owner_source = path.parent
    try:
        owner = owner_source.stat()
        if hasattr(os, "chown") and os.geteuid() == 0:
            os.chown(path, owner.st_uid, owner.st_gid)
        os.chmod(path, 0o600)
    except OSError as exc:
        log(f"Could not set strict host kubeconfig ownership on {path}: {exc!r}; falling back to user-readable mode.")
        try:
            os.chmod(path, 0o644)
        except OSError as chmod_exc:
            log(f"Could not update host kubeconfig permissions on {path}: {chmod_exc!r}")

    stat_result = path.stat()
    return {
        "uid": stat_result.st_uid,
        "gid": stat_result.st_gid,
        "mode": oct(stat_result.st_mode & 0o777),
    }


def update_runtime_info_host_kubeconfig(path: Path, server: str, mode: str) -> None:
    """Add host kubeconfig details to the per-lab runtime info file."""
    info_file = runtime_dir(CONFIG, "info")
    try:
        data = json.loads(info_file.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return

    host_runtime_dir = config_str(CONFIG, "HOST_RUNTIME_DIR", "", allow_empty=True).strip()
    host_path = str(Path(host_runtime_dir) / path.name) if host_runtime_dir else ""
    data["host_kubeconfig"] = {
        "path": str(path),
        "host_path": host_path,
        "server": server,
        "mode": mode,
        "current_context": kube_context_for_cluster(kind_cluster_name(CONFIG)),
    }

    info_file.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def export_host_kubeconfig(name: str) -> None:
    """Export a per-lab kubeconfig that is ready for kubectl on the host."""
    out_file = runtime_dir(CONFIG, "kubeconfig")
    context = kube_context_for_cluster(name)
    out_file.parent.mkdir(parents=True, exist_ok=True)

    completed = run_cmd(
        ["kind", "export", "kubeconfig", "--name", name, "--kubeconfig", str(out_file)],
        check=False,
        capture_output=True,
        config=CONFIG,
    )

    if completed.returncode != 0:
        log("Could not export host kubeconfig; continuing without it.")
        set_state_value(STATE, "host_kubeconfig", {"path": str(out_file), "created": False})
        return

    original_server = kubeconfig_server(out_file, context)
    server, mode, proxy_state = host_kube_api_server(name, original_server)
    if server:
        set_kubeconfig_server(out_file, context, server)
    permission_state = make_host_user_readable(out_file)
    update_runtime_info_host_kubeconfig(out_file, server, mode)

    set_state_value(STATE, "kube_api_proxy_target", proxy_state)
    set_state_value(
        STATE,
        "host_kubeconfig",
        {
            "path": str(out_file),
            "created": True,
            "server": server,
            "original_server": original_server,
            "mode": mode,
            "current_context": context,
            "permissions": permission_state,
        },
    )
    log(f"Host kubeconfig exported: {out_file} ({mode}, server={server})")


def docker_container_statuses() -> dict[str, str]:
    """Return Docker container status strings keyed by container name."""
    completed = run_cmd(
        ["docker", "ps", "--all", "--format", "{{.Names}}\t{{.Status}}"],
        check=False,
        capture_output=True,
        quiet=True,
        config=CONFIG,
    )

    statuses: dict[str, str] = {}
    for line in completed.stdout.splitlines():
        name, sep, status = line.partition("\t")
        if sep and name.strip():
            statuses[name.strip()] = status.strip()

    return statuses


def wait_for_kind_containers_up(name: str, context: str) -> None:
    """Wait until every Kind node container reports Docker status Up."""
    CONFIG["KUBE_CONTEXT"] = context
    timeout = config_int(CONFIG, "KIND_CONTAINER_UP_TIMEOUT_SECONDS", 180, minimum=1)
    deadline = time.monotonic() + timeout
    last_problem = ""

    while time.monotonic() < deadline:
        nodes = kind_nodes(name, CONFIG)
        statuses = docker_container_statuses()

        if not nodes:
            last_problem = f"no Kind containers found for cluster {name}"
            time.sleep(1)
            continue

        missing = [node for node in nodes if node not in statuses]
        not_up = {node: statuses[node] for node in nodes if node in statuses and not statuses[node].startswith("Up")}

        if not missing and not not_up:
            set_state_value(
                STATE,
                "kind_container_up_wait",
                {
                    "cluster": name,
                    "containers": {node: statuses[node] for node in nodes},
                    "timeout_seconds": timeout,
                },
            )
            log("All Kind node containers are Up: " + ", ".join(nodes))
            return

        parts = []
        if missing:
            parts.append("missing=" + ", ".join(missing))
        if not_up:
            parts.append("not_up=" + ", ".join(f"{node}={status}" for node, status in not_up.items()))
        last_problem = "; ".join(parts)
        time.sleep(1)

    die(
        f"Kind node containers did not reach Docker status Up within {timeout}s: "
        f"{last_problem or 'no status details'}"
    )


def save_cluster_state(name: str, context: str) -> None:
    """Save Kind cluster information into STATE."""
    nodes = kind_nodes(name, CONFIG)
    control_plane = next(
        (node for node in nodes if node.endswith("control-plane")),
        "",
    )

    set_state_value(STATE, "kind_cluster", name)
    set_state_value(STATE, "kube_context", context)
    set_state_value(STATE, "kind_nodes", nodes)
    set_state_value(STATE, "control_plane_node", control_plane)


def main() -> None:
    name = kind_cluster_name(CONFIG)
    context = kube_context_for_cluster(name)
    ensure_docker_network(config_str(CONFIG, "CP_NETWORK", f"kind-{name}", allow_empty=False), CONFIG)

    kind_config = write_kind_config(name)

    create_or_reuse_cluster(name, kind_config)
    wait_for_kind_containers_up(name, context)

    export_host_kubeconfig(name)

    save_cluster_state(name, context)


if __name__ == "__main__":
    main()
