#!/usr/bin/env python3
"""Prepare the OpenTelemetry Skaffold build and deploy environment.

Before the generic Skaffold step runs, this hook exposes CONFIG and STATE values
for template rendering, clears stale Helm release locks, starts the build-helper
when required, and stores Docker environment overrides used for local image builds
and registry pushes."""

from lib import (
    clear_pending_helm_release,
    config_bool,
    config_str,
    kube_context_name,
    log,
    python_value_to_env,
    registry_endpoint,
    run_cmd,
    set_state_value,
    skaffold_runtime_env,
    start_cluster_build_helper,
    state_values,
)


def skaffold_state_env() -> dict[str, str]:
    """Expose uppercase STATE values to Skaffold rendering."""
    return {
        name: python_value_to_env(value)
        for name, value in state_values(STATE).items()
        if isinstance(name, str) and name.isupper()
    }


def prepare_skaffold_render_env() -> None:
    """Prepare the env used later by 03 for render/build/deploy."""
    env = skaffold_runtime_env(CONFIG)
    state_env = skaffold_state_env()
    env.update(state_env)
    if "CILIUM_ENABLED" in CONFIG:
        env["CILIUM_ENABLED"] = (
            "true" if config_bool(CONFIG, "CILIUM_ENABLED", False) else "false"
        )
    else:
        env["CILIUM_ENABLED"] = "false"

    set_state_value(STATE, "skaffold_render_env", env)
    set_state_value(STATE, "skaffold_state_env_keys", sorted(state_env.keys()))
    set_state_value(STATE, "cilium_skaffold_enabled", env.get("CILIUM_ENABLED") == "true")


def docker_login_registry(docker_env: dict[str, str]) -> None:
    """Authenticate Docker CLI to the local registry when credentials exist."""
    username = config_str(CONFIG, "REGISTRY_USER", "", allow_empty=True).strip()
    password = config_str(CONFIG, "REGISTRY_PASS", "", allow_empty=True).strip()

    if not username:
        return

    run_cmd(
        ["docker", "login", registry_endpoint(CONFIG), "-u", username, "--password-stdin"],
        input_text=f"{password}\n",
        config=CONFIG,
        env=docker_env,
    )


def prepare_skaffold_build_env() -> None:
    """Start build helper and save only Docker env overrides for the generic step."""
    helper_env = start_cluster_build_helper(CONFIG)
    docker_login_registry(helper_env)

    set_state_value(
        STATE,
        "skaffold_build_env_overrides",
        {
            "DOCKER_HOST": helper_env.get("DOCKER_HOST", ""),
            "DOCKER_TLS_VERIFY": helper_env.get("DOCKER_TLS_VERIFY", ""),
            "DOCKER_CERT_PATH": helper_env.get("DOCKER_CERT_PATH", ""),
        },
    )
    set_state_value(STATE, "skaffold_build_helper_started", True)


def clear_helm_locks() -> None:
    """Clear stale OpenTelemetry Helm release locks before Skaffold deploy."""
    context = kube_context_name(CONFIG)
    mem_namespace = config_str(CONFIG, "MEM_NAMESPACE", "mem")

    clear_pending_helm_release(context, "metallb", "metallb-system", config=CONFIG)
    clear_pending_helm_release(context, "csi-driver-smb", "kube-system", config=CONFIG)
    clear_pending_helm_release(context, "honeypot-additions", "default", config=CONFIG)
    clear_pending_helm_release(context, "honeypot-telemetry", mem_namespace, config=CONFIG)
    clear_pending_helm_release(context, "honeypot-astronomy-shop", "default", config=CONFIG)

    set_state_value(STATE, "otel_pre_deploy_cleanup", True)


def main() -> None:
    clear_helm_locks()
    prepare_skaffold_render_env()
    prepare_skaffold_build_env()


if __name__ == "__main__":
    main()
