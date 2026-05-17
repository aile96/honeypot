#!/usr/bin/env python3
"""Expose the lab-controller helper library through a single import point.

The pipeline and hooks import most helpers from lib for compatibility with older
scripts. This package re-exports logging, command execution, Docker, Kubernetes,
Compose, Helm, registry, state, and target utilities while keeping their actual
implementations split across focused modules."""

from .logging import (
    die,
    err,
    log,
    timestamp,
    warn,
)

from .config import (
    Config,
    bool_config_value,
    config_bool,
    config_float,
    config_int,
    config_path,
    config_str,
    config_to_env,
    is_true as config_is_true,
    load_default_config,
    load_variables_file,
    load_variables_file_if_exists,
    load_variables_file_if_exists as load_config_file_if_exists,
    merge_config,
    normalize_bool_config,
    parse_bool_value,
    parse_positive_float_value,
    python_value_to_env,
    require_config,
    require_port_config,
    set_config_default,
    validate_config_name,
)

from .env import (
    bool_env_value,
    is_true,
    load_default_variables,
    load_variables_file_if_exists as load_env_variables_file_if_exists,
    normalize_bool_env,
    require_env,
)

from .command import (
    CommandError,
    require_command,
    run_cmd,
    run_cmd_or_raise,
)

from .validation import (
    require_non_negative_int,
    require_int_at_least,
    require_port_env,
)

from .retry import (
    retry_operation,
    retry_operation_or_raise,
)

from .docker import (
    HONEYPOT_DOCKER_IP_CACHE,
    HONEYPOT_DOCKER_IP_CACHE_READY,
    docker_build_image,
    docker_bind_source,
    docker_container_exists,
    docker_container_networks,
    docker_first_container_ip,
    docker_image_exists,
    docker_network_exists,
    docker_running_container_exists,
    ensure_container_network_connected,
    ensure_docker_network,
    find_docker_container_by_ip,
    is_running_in_container,
    refresh_docker_ip_cache,
    remove_container_if_exists,
)

from .docker_runtime import (
    DEFAULT_DOCKER_DATA_ROOT,
    DEFAULT_DOCKERD_LOG_FILE,
    docker_env,
    require_existing_unix_socket,
    start_internal_dockerd,
    wait_for_docker_ready,
)

from .cleanup import (
    cleanup_docker_compose_stack,
    cleanup_kind_cluster,
    cleanup_underlay_containers,
)

from .hosts import (
    ensure_hosts_mapping,
)

from .kind_template import (
    optional_config_bool,
    prepare_control_plane_patch_template_variables,
    prepare_kind_template_defaults,
    registry_extra_mounts_block,
    worker_nodes_block,
)

from .kubernetes import (
    get_current_kube_context,
    kind_cmd,
    kind_cluster_name,
    kind_clusters,
    kind_nodes,
    kube_context_name,
    kubectl,
    kubectl_config_view_jsonpath,
    kubectl_ctx,
    kubectl_get,
    kubectl_get_or_raise,
    node_internal_ip,
)

from .registry import (
    image_version,
    registry_endpoint,
)

from .build_helper import (
    docker_host_env,
    start_cluster_build_helper,
    stop_cluster_build_helper,
    wait_docker_host_ready,
)

from .skaffold_config import (
    render_skaffold_config,
    skaffold_artifacts_path,
    skaffold_config_path,
    skaffold_rendered_config_path,
    skaffold_runtime_env,
    skaffold_template_path,
    skaffold_workdir,
)

from .compose import (
    compose_build,
    compose_down,
    compose_environment,
    compose_file_path,
    compose_project_name,
    compose_up,
    configured_compose_services,
    discover_compose_services,
    docker_compose_command,
    docker_compose_supports_wait,
)

from .target import (
    config_list,
    generated_dir,
    runtime_dir,
    target_conf_dir,
    target_conf_file,
    target_root,
)

from .service import wait_http

from .helm import (
    clear_pending_helm_release,
    helm_ctx,
)

from .pipeline import (
    discover_pipeline_scripts,
    resolve_hook_candidates,
    resolve_step_retry_policy,
)

from .process import (
    run_python_child,
    send_signal_to_process_group,
    terminate_process,
)

from .state import (
    State,
    get_state_value,
    load_or_create_state,
    load_state_file,
    mark_pipeline_failed,
    mark_pipeline_ready,
    new_state,
    record_step_finish,
    record_step_start,
    record_unit_finish,
    record_unit_start,
    save_state_file,
    set_state_value,
    state_values,
    unit_completed,
    utc_timestamp,
)

from .utils import (
    resolve_project_path,
    substitute_vars,
)

__all__ = [
    "die",
    "err",
    "log",
    "timestamp",
    "warn",
    "Config",
    "bool_config_value",
    "config_bool",
    "config_float",
    "config_int",
    "config_path",
    "config_str",
    "config_to_env",
    "config_is_true",
    "load_default_config",
    "load_variables_file",
    "load_config_file_if_exists",
    "merge_config",
    "normalize_bool_config",
    "parse_bool_value",
    "parse_positive_float_value",
    "python_value_to_env",
    "require_config",
    "require_port_config",
    "set_config_default",
    "validate_config_name",
    "bool_env_value",
    "is_true",
    "load_default_variables",
    "load_env_variables_file_if_exists",
    "load_variables_file_if_exists",
    "normalize_bool_env",
    "require_env",
    "CommandError",
    "require_command",
    "run_cmd",
    "run_cmd_or_raise",
    "require_non_negative_int",
    "require_int_at_least",
    "require_port_env",
    "retry_operation",
    "retry_operation_or_raise",
    "HONEYPOT_DOCKER_IP_CACHE",
    "HONEYPOT_DOCKER_IP_CACHE_READY",
    "docker_build_image",
    "docker_bind_source",
    "docker_container_exists",
    "docker_container_networks",
    "docker_first_container_ip",
    "docker_image_exists",
    "docker_network_exists",
    "docker_running_container_exists",
    "ensure_container_network_connected",
    "ensure_docker_network",
    "find_docker_container_by_ip",
    "is_running_in_container",
    "refresh_docker_ip_cache",
    "remove_container_if_exists",
    "DEFAULT_DOCKER_DATA_ROOT",
    "DEFAULT_DOCKERD_LOG_FILE",
    "docker_env",
    "require_existing_unix_socket",
    "start_internal_dockerd",
    "wait_for_docker_ready",
    "cleanup_kind_cluster",
    "cleanup_docker_compose_stack",
    "cleanup_underlay_containers",
    "ensure_hosts_mapping",
    "optional_config_bool",
    "prepare_control_plane_patch_template_variables",
    "prepare_kind_template_defaults",
    "registry_extra_mounts_block",
    "worker_nodes_block",
    "get_current_kube_context",
    "kind_cmd",
    "kind_cluster_name",
    "kind_clusters",
    "kind_nodes",
    "kube_context_name",
    "kubectl",
    "kubectl_config_view_jsonpath",
    "kubectl_ctx",
    "kubectl_get",
    "kubectl_get_or_raise",
    "node_internal_ip",
    "image_version",
    "registry_endpoint",
    "docker_host_env",
    "start_cluster_build_helper",
    "stop_cluster_build_helper",
    "wait_docker_host_ready",
    "render_skaffold_config",
    "skaffold_artifacts_path",
    "skaffold_config_path",
    "skaffold_rendered_config_path",
    "skaffold_runtime_env",
    "skaffold_template_path",
    "skaffold_workdir",
    "compose_build",
    "compose_down",
    "compose_environment",
    "compose_file_path",
    "compose_project_name",
    "compose_up",
    "configured_compose_services",
    "discover_compose_services",
    "docker_compose_command",
    "docker_compose_supports_wait",
    "config_list",
    "generated_dir",
    "runtime_dir",
    "target_conf_dir",
    "target_conf_file",
    "target_root",
    "wait_http",
    "clear_pending_helm_release",
    "helm_ctx",
    "discover_pipeline_scripts",
    "resolve_hook_candidates",
    "resolve_step_retry_policy",
    "run_python_child",
    "send_signal_to_process_group",
    "terminate_process",
    "State",
    "get_state_value",
    "load_or_create_state",
    "load_state_file",
    "mark_pipeline_failed",
    "mark_pipeline_ready",
    "new_state",
    "record_step_finish",
    "record_step_start",
    "record_unit_finish",
    "record_unit_start",
    "save_state_file",
    "set_state_value",
    "state_values",
    "unit_completed",
    "utc_timestamp",
    "resolve_project_path",
    "substitute_vars",
]
