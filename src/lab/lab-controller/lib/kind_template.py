#!/usr/bin/env python3
"""Render reusable Kind template snippets for target hooks."""

from __future__ import annotations

from typing import Any, Mapping, MutableMapping

from .config import Config, config_bool, config_int, config_str
from .kubernetes import kind_cluster_name
from .logging import die
from .state import State, set_state_value


def optional_config_bool(config: Mapping[str, Any], name: str, default: bool = False) -> bool:
    """Return a boolean CONFIG value, accepting common text representations."""
    if name not in config:
        return default
    raw_default = "true" if default else "false"
    raw = config_str(config, name, raw_default).strip().lower()

    if raw in {"1", "true", "yes", "y", "on", "enabled"}:
        return True
    if raw in {"0", "false", "no", "n", "off", "disabled"}:
        return False

    die(f"{name} must be a boolean value, got: {raw!r}")


def registry_extra_mounts_block(config: Config, indent: str = "  ") -> str:
    """Return a Kind extraMounts block for mounted containerd registry config."""
    source = config_str(config, "KIND_CONTAINERD_CERTS_DIR", "", allow_empty=True).strip()
    if not source:
        return ""

    return f"""{indent}extraMounts:
{indent}- hostPath: {source}
{indent}  containerPath: /etc/containerd/certs.d
{indent}  readOnly: true
"""


def kind_disable_default_cni_block(config: Config) -> str:
    """Return the Kind networking block used when an external CNI is enabled."""
    return "  disableDefaultCNI: true\n" if optional_config_bool(config, "CILIUM_ENABLED", False) else ""


def open_ports_kubelet_patch(config: Config) -> str:
    """Return the worker kubelet patch that exposes the read-only port when enabled."""
    if not optional_config_bool(config, "OPEN_PORTS", False):
        return ""
    return """  kubeadmConfigPatches:
  - |-
    kind: JoinConfiguration
    nodeRegistration:
      kubeletExtraArgs:
        address: "0.0.0.0"
        read-only-port: "10255"
"""


def worker_node_block(config: Config, *, include_open_ports: bool = False) -> str:
    """Return one Kind worker node block."""
    block = "- role: worker\n"
    block += registry_extra_mounts_block(config, "  ")
    if include_open_ports:
        block += open_ports_kubelet_patch(config)
    return block


def worker_nodes_block(config: Config, workers: int, *, include_open_ports: bool = False) -> str:
    """Return all requested Kind worker node blocks."""
    return "".join(worker_node_block(config, include_open_ports=include_open_ports) for _ in range(workers))


def prepare_kind_template_defaults(
    config: Config,
    state: State,
    *,
    profile_name: str | None = None,
    default_workers: int = 1,
    minimum_workers: int = 1,
    include_open_ports: bool = False,
    state_key: str = "kind_default_template_variables",
) -> None:
    """Install reusable derived Kind template values into CONFIG."""
    name = profile_name or kind_cluster_name(config)
    registry_port = config_str(config, "REGISTRY_PORT", 5000, allow_empty=False)
    registry_name = config_str(config, "REGISTRY_NAME", "registry", allow_empty=False)
    registry = f"{registry_name}:{registry_port}"
    api_port = config_str(config, "KIND_API_SERVER_PORT", "").strip()
    workers = config_int(config, "WORKERS", default_workers, minimum=minimum_workers)

    config.setdefault("KIND_REGISTRY_ENDPOINT", registry)
    config.setdefault("KIND_REGISTRY_MIRROR_ENDPOINT", registry)
    config.setdefault("KIND_REGISTRY_MIRROR_URL", f"https://{registry}")
    config.setdefault(
        "KIND_API_SERVER_PORT_BLOCK",
        f"  apiServerPort: {api_port}\n" if api_port else "",
    )
    config.setdefault("KIND_DISABLE_DEFAULT_CNI_BLOCK", kind_disable_default_cni_block(config))
    config.setdefault("KIND_CONTROL_PLANE_EXTRA_MOUNTS_BLOCK", registry_extra_mounts_block(config, "  "))
    config.setdefault(
        "KIND_WORKER_NODES_BLOCK",
        worker_nodes_block(config, workers, include_open_ports=include_open_ports),
    )

    state_payload: dict[str, object] = {
        "LAB_NAME": name,
        "CLUSTER_PROFILE": name,
        "KIND_REGISTRY_ENDPOINT": config["KIND_REGISTRY_ENDPOINT"],
        "KIND_REGISTRY_MIRROR_ENDPOINT": config["KIND_REGISTRY_MIRROR_ENDPOINT"],
        "KIND_REGISTRY_MIRROR_URL": config["KIND_REGISTRY_MIRROR_URL"],
        "KIND_API_SERVER_PORT_BLOCK": config["KIND_API_SERVER_PORT_BLOCK"],
        "KIND_DISABLE_DEFAULT_CNI_BLOCK": config["KIND_DISABLE_DEFAULT_CNI_BLOCK"],
        "KIND_CONTROL_PLANE_EXTRA_MOUNTS_BLOCK": config["KIND_CONTROL_PLANE_EXTRA_MOUNTS_BLOCK"],
        "KIND_WORKER_NODES_BLOCK": config["KIND_WORKER_NODES_BLOCK"],
    }
    if "CILIUM_ENABLED" in config:
        state_payload["CILIUM_ENABLED"] = optional_config_bool(config, "CILIUM_ENABLED", False)

    set_state_value(state, state_key, state_payload)


def build_etcd_exposure_patch(config: Config) -> tuple[str, str, dict[str, object]]:
    """Build Kind patch and port mapping blocks for optional unauthenticated etcd."""
    if not optional_config_bool(config, "ETCD_EXPOSURE", False):
        return "", "", {"enabled": False}

    plain_port = config_int(config, "PLAIN_PORT", 12379, minimum=1, maximum=65535)
    container_port = config_int(
        config,
        "ETCD_EXPOSURE_CONTAINER_PORT",
        plain_port,
        minimum=1,
        maximum=65535,
    )
    host_port_raw = config_str(config, "ETCD_EXPOSURE_HOST_PORT", "").strip()
    host_port = (
        config_int(config, "ETCD_EXPOSURE_HOST_PORT", minimum=1, maximum=65535)
        if host_port_raw
        else 0
    )
    host_address = config_str(config, "ETCD_EXPOSURE_HOST_ADDRESS", "127.0.0.1", allow_empty=False)
    listen_address = config_str(config, "ETCD_EXPOSURE_LISTEN_ADDRESS", "0.0.0.0", allow_empty=False)
    secure_client_urls = config_str(
        config,
        "ETCD_EXPOSURE_SECURE_CLIENT_URLS",
        "https://127.0.0.1:2379",
        allow_empty=False,
    )
    advertise_client_urls = config_str(
        config,
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

    port_mapping = ""
    if host_port:
        port_mapping = f"""  extraPortMappings:
  - containerPort: {container_port}
    hostPort: {host_port}
    listenAddress: "{host_address}"
    protocol: TCP
"""

    state_payload = {
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
    return kubeadm_config_patch, port_mapping, state_payload


def anonymous_auth_patch(config: Config) -> tuple[str, dict[str, object]]:
    """Build the optional kube-apiserver anonymous-auth Kind patch."""
    if not optional_config_bool(config, "ANONYMOUS_AUTH", False):
        return "", {"enabled": False}
    return (
        """- |-
  apiVersion: kubeadm.k8s.io/v1beta3
  kind: ClusterConfiguration
  apiServer:
    extraArgs:
      anonymous-auth: "true"
""",
        {
            "enabled": True,
            "kubeadm_config_patches_variable": "KIND_ETCD_UNAUTHENTICATED_KUBEADM_CONFIG_PATCHES_BLOCK",
        },
    )


def prepare_control_plane_patch_template_variables(
    config: Config,
    state: State,
    *,
    include_anonymous_auth: bool = False,
) -> None:
    """Populate CONFIG with optional control-plane kubeadm patch template blocks."""
    config["KIND_ETCD_UNAUTHENTICATED_KUBEADM_CONFIG_PATCH_BLOCK"] = ""
    config["KIND_ETCD_UNAUTHENTICATED_KUBEADM_CONFIG_PATCHES_BLOCK"] = ""
    config["KIND_ETCD_UNAUTHENTICATED_PORT_MAPPING_BLOCK"] = ""

    patches: list[str] = []
    etcd_patch, port_mapping, etcd_state = build_etcd_exposure_patch(config)
    if etcd_patch:
        patches.append(etcd_patch)
        config["KIND_ETCD_UNAUTHENTICATED_KUBEADM_CONFIG_PATCH_BLOCK"] = etcd_patch
        config["KIND_ETCD_UNAUTHENTICATED_PORT_MAPPING_BLOCK"] = port_mapping

    anonymous_state: dict[str, object] = {"enabled": False}
    if include_anonymous_auth:
        patch, anonymous_state = anonymous_auth_patch(config)
        if patch:
            patches.append(patch)

    if patches:
        config["KIND_ETCD_UNAUTHENTICATED_KUBEADM_CONFIG_PATCHES_BLOCK"] = (
            "kubeadmConfigPatches:\n" + "".join(patches)
        )

    set_state_value(state, "kind_etcd_exposure", etcd_state)
    if include_anonymous_auth:
        set_state_value(state, "kind_anonymous_auth", anonymous_state)
