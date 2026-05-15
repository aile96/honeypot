#!/usr/bin/env python3
"""Prepare OpenTelemetry-specific Kind template variables.

This hook renders the dynamic YAML snippets consumed by kind-cluster.yaml.tmpl,
including registry mirror settings, containerd cert mounts, optional API-server
ports, CNI settings, etcd exposure, and worker-node definitions required by the
OpenTelemetry attack scenarios."""

from lib import (
    config_int,
    config_str,
    die,
    kind_cluster_name,
    log,
    set_state_value,
)


def config_bool(name: str, default: bool = False) -> bool:
    """Return a boolean CONFIG value."""
    raw_default = "true" if default else "false"
    raw = config_str(CONFIG, name, raw_default).strip().lower()

    if raw in {"1", "true", "yes", "y", "on", "enabled"}:
        return True

    if raw in {"0", "false", "no", "n", "off", "disabled"}:
        return False

    die(f"{name} must be a boolean value, got: {raw!r}")


def registry_extra_mounts_block(indent: str = "  ") -> str:
    """Return a Kind extraMounts block for containerd registry config."""
    source = config_str(CONFIG, "KIND_CONTAINERD_CERTS_DIR", "", allow_empty=True).strip()
    if not source:
        return ""

    return f"""{indent}extraMounts:
{indent}- hostPath: {source}
{indent}  containerPath: /etc/containerd/certs.d
{indent}  readOnly: true
"""


def cilium_enabled() -> bool:
    """Return whether the target explicitly asked for Cilium."""
    return "CILIUM_ENABLED" in CONFIG and config_bool("CILIUM_ENABLED", False)


def kind_disable_default_cni_block() -> str:
    """Return the Kind networking block needed when Cilium owns pod networking."""
    return "  disableDefaultCNI: true\n" if cilium_enabled() else ""


def worker_node_block() -> str:
    """Return one Kind worker node block."""
    block = "- role: worker\n"

    mounts = registry_extra_mounts_block("  ")
    if mounts:
        block += mounts

    if config_bool("OPEN_PORTS", False):
        block += """  kubeadmConfigPatches:
  - |-
    kind: JoinConfiguration
    nodeRegistration:
      kubeletExtraArgs:
        address: "0.0.0.0"
        read-only-port: "10255"
"""

    return block


def worker_nodes_block(workers: int) -> str:
    """Return all Kind worker node blocks."""
    return "".join(worker_node_block() for _ in range(workers))


def prepare_kind_template_defaults(name: str) -> None:
    """Install generic derived Kind template values when hooks did not set them."""
    registry_port = config_str(CONFIG, "REGISTRY_PORT", 5000, allow_empty=False)
    registry_name = config_str(CONFIG, "REGISTRY_NAME", "registry", allow_empty=False)
    registry = f"{registry_name}:{registry_port}"
    registry_scheme = "https"

    api_port = config_str(CONFIG, "KIND_API_SERVER_PORT", "").strip()
    workers = config_int(CONFIG, "WORKERS", 2, minimum=2)

    CONFIG.setdefault("KIND_REGISTRY_ENDPOINT", registry)
    CONFIG.setdefault("KIND_REGISTRY_MIRROR_ENDPOINT", registry)
    CONFIG.setdefault("KIND_REGISTRY_MIRROR_URL", f"{registry_scheme}://{registry}")
    CONFIG.setdefault(
        "KIND_API_SERVER_PORT_BLOCK",
        f"  apiServerPort: {api_port}\n" if api_port else "",
    )
    CONFIG.setdefault("KIND_DISABLE_DEFAULT_CNI_BLOCK", kind_disable_default_cni_block())
    CONFIG.setdefault("KIND_CONTROL_PLANE_EXTRA_MOUNTS_BLOCK", registry_extra_mounts_block("  "))
    CONFIG.setdefault("KIND_WORKER_NODES_BLOCK", worker_nodes_block(workers))

    set_state_value(
        STATE,
        "kind_default_template_variables",
        {
            "CLUSTER_PROFILE": name,
            "KIND_REGISTRY_ENDPOINT": CONFIG["KIND_REGISTRY_ENDPOINT"],
            "KIND_REGISTRY_MIRROR_ENDPOINT": CONFIG["KIND_REGISTRY_MIRROR_ENDPOINT"],
            "KIND_REGISTRY_MIRROR_URL": CONFIG["KIND_REGISTRY_MIRROR_URL"],
            "KIND_API_SERVER_PORT_BLOCK": CONFIG["KIND_API_SERVER_PORT_BLOCK"],
            "KIND_DISABLE_DEFAULT_CNI_BLOCK": CONFIG["KIND_DISABLE_DEFAULT_CNI_BLOCK"],
            "KIND_CONTROL_PLANE_EXTRA_MOUNTS_BLOCK": CONFIG[
                "KIND_CONTROL_PLANE_EXTRA_MOUNTS_BLOCK"
            ],
            "KIND_WORKER_NODES_BLOCK": CONFIG["KIND_WORKER_NODES_BLOCK"],
            "CILIUM_ENABLED": cilium_enabled(),
        },
    )


def prepare_etcd_exposure_template_variables() -> None:
    """Add Kind kubeadm patch variables for legacy control-plane toggles."""
    CONFIG["KIND_ETCD_UNAUTHENTICATED_KUBEADM_CONFIG_PATCH_BLOCK"] = ""
    CONFIG["KIND_ETCD_UNAUTHENTICATED_KUBEADM_CONFIG_PATCHES_BLOCK"] = ""
    CONFIG["KIND_ETCD_UNAUTHENTICATED_PORT_MAPPING_BLOCK"] = ""

    patches: list[str] = []
    etcd_state: dict[str, object] = {"enabled": False}
    anonymous_state: dict[str, object] = {"enabled": False}

    if config_bool("ETCD_EXPOSURE", False):
        plain_port = config_int(CONFIG, "PLAIN_PORT", 12379, minimum=1, maximum=65535)
        container_port = config_int(
            CONFIG,
            "ETCD_EXPOSURE_CONTAINER_PORT",
            plain_port,
            minimum=1,
            maximum=65535,
        )
        host_port_raw = config_str(CONFIG, "ETCD_EXPOSURE_HOST_PORT", "").strip()
        if host_port_raw:
            host_port = config_int(CONFIG, "ETCD_EXPOSURE_HOST_PORT", minimum=1, maximum=65535)
        else:
            host_port = 0

        host_address = config_str(
            CONFIG,
            "ETCD_EXPOSURE_HOST_ADDRESS",
            "127.0.0.1",
            allow_empty=False,
        )
        listen_address = config_str(
            CONFIG,
            "ETCD_EXPOSURE_LISTEN_ADDRESS",
            "0.0.0.0",
            allow_empty=False,
        )

        secure_client_urls = config_str(
            CONFIG,
            "ETCD_EXPOSURE_SECURE_CLIENT_URLS",
            "https://127.0.0.1:2379",
            allow_empty=False,
        )
        advertise_client_urls = config_str(
            CONFIG,
            "ETCD_EXPOSURE_ADVERTISE_CLIENT_URLS",
            "https://127.0.0.1:2379",
            allow_empty=False,
        )

        kubeadm_config_patch = f"""- |-
  apiVersion: kubeadm.k8s.io/v1beta3
  kind: ClusterConfiguration
  etcd:
    local:
      extraArgs:
        # Keep the secure endpoint used by the kube-apiserver.
        # Add an unauthenticated HTTP endpoint for local testing only.
        listen-client-urls: "{secure_client_urls},http://{listen_address}:{container_port}"
        # Do not advertise the unauthenticated endpoint to the control plane.
        advertise-client-urls: "{advertise_client_urls}"
"""
        patches.append(kubeadm_config_patch)
        CONFIG["KIND_ETCD_UNAUTHENTICATED_KUBEADM_CONFIG_PATCH_BLOCK"] = kubeadm_config_patch

        if host_port:
            CONFIG["KIND_ETCD_UNAUTHENTICATED_PORT_MAPPING_BLOCK"] = f"""  extraPortMappings:
  - containerPort: {container_port}
    hostPort: {host_port}
    listenAddress: "{host_address}"
    protocol: TCP
"""
        etcd_state = {
            "enabled": True,
            "container_port": container_port,
            "host_port": host_port or "",
            "host_address": host_address,
            "listen_address": listen_address,
            "secure_client_urls": secure_client_urls,
            "advertise_client_urls": advertise_client_urls,
            "kubeadm_config_patch_variable": "KIND_ETCD_UNAUTHENTICATED_KUBEADM_CONFIG_PATCH_BLOCK",
            "kubeadm_config_patches_variable": "KIND_ETCD_UNAUTHENTICATED_KUBEADM_CONFIG_PATCHES_BLOCK",
            "port_mapping_variable": "KIND_ETCD_UNAUTHENTICATED_PORT_MAPPING_BLOCK",
        }

    if config_bool("ANONYMOUS_AUTH", False):
        anonymous_patch = """- |-
  apiVersion: kubeadm.k8s.io/v1beta3
  kind: ClusterConfiguration
  apiServer:
    extraArgs:
      anonymous-auth: "true"
"""
        patches.append(anonymous_patch)
        anonymous_state = {
            "enabled": True,
            "kubeadm_config_patches_variable": "KIND_ETCD_UNAUTHENTICATED_KUBEADM_CONFIG_PATCHES_BLOCK",
        }

    if patches:
        CONFIG["KIND_ETCD_UNAUTHENTICATED_KUBEADM_CONFIG_PATCHES_BLOCK"] = (
            "kubeadmConfigPatches:\n" + "".join(patches)
        )

    set_state_value(STATE, "kind_etcd_exposure", etcd_state)
    set_state_value(STATE, "kind_anonymous_auth", anonymous_state)


def main() -> None:
    log("Preparing default Kind template variables.")
    prepare_kind_template_defaults(kind_cluster_name(CONFIG))
    prepare_etcd_exposure_template_variables()


if __name__ == "__main__":
    main()
