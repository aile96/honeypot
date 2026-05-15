#!/usr/bin/env python3
"""Render the target Kind configuration and create or reuse the Kind cluster.

This step turns kind-cluster.yaml.tmpl into a concrete YAML file, creates the Kind
cluster when it is missing, exports kubeconfig files, and waits until every Kind
node container reports Docker status "Up". Kubernetes-level readiness is left to
later deployment checks; this step only verifies that the cluster containers are
alive."""

import re
import time
from pathlib import Path

from lib import (
    CommandError,
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
    out = generated_dir(CONFIG) / "kind" / f"{name}.yaml"
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
    cmd = [
        "kind",
        "create",
        "cluster",
        "--name",
        name,
        "--config",
        str(kind_config),
        "--wait",
        "180s",
    ]

    image = config_str(CONFIG, "K8S_IMAGE", "").strip()
    if image:
        cmd.extend(["--image", image])

    run_cmd(
        cmd,
        timeout_seconds=300,
        config=CONFIG,
    )

    log(f"Created Kind cluster {name}.")
    export_cluster_kubeconfig(name)


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


def export_host_kubeconfig(name: str) -> None:
    """Best-effort host-reachable kubeconfig exported to /res/runtime/kubeconfig."""
    out_file = runtime_dir(CONFIG, "kubeconfig")
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

    exposed = config_str(CONFIG, "EXPOSE_TO_HOST", "false").strip().lower() in {"1", "true", "yes", "y", "on"}
    host = config_str(CONFIG, "HOST_KUBE_API_SERVER_HOST", "127.0.0.1", allow_empty=False)
    port = (
        config_str(CONFIG, "KIND_API_SERVER_PORT", "", allow_empty=True).strip()
        or config_str(CONFIG, "CONTROL_PLANE_PORT", "6443", allow_empty=False).strip()
        or "6443"
    )

    if exposed:
        content = out_file.read_text(encoding="utf-8")
        content = re.sub(r"server:\s+https://[^\s]+", f"server: https://{host}:{port}", content)
        out_file.write_text(content, encoding="utf-8")

    set_state_value(
        STATE,
        "host_kubeconfig",
        {
            "path": str(out_file),
            "created": True,
            "host_rewritten": exposed,
            "server": f"https://{host}:{port}" if exposed else "kind-export-default",
        },
    )
    log(f"Host kubeconfig exported: {out_file}")


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
    ensure_docker_network("kind", CONFIG)

    kind_config = write_kind_config(name)

    create_or_reuse_cluster(name, kind_config)

    export_host_kubeconfig(name)

    wait_for_kind_containers_up(name, context)

    save_cluster_state(name, context)


if __name__ == "__main__":
    main()
