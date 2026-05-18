#!/usr/bin/env python3
"""Prepare the 5Gcore Skaffold build and deploy environment.

This hook runs before the generic Skaffold step. It clears interrupted Helm
releases, exposes render-time environment variables, starts the Docker build-helper
when needed, and logs in to the local HTTPS registry so Skaffold can build and
push the wrapper images under src/5Gcore/containers."""

from lib import (
    clear_pending_helm_release,
    config_bool,
    config_str,
    kube_context_name,
    log,
    registry_endpoint,
    run_cmd,
    skaffold_runtime_env,
    set_state_value,
    start_cluster_build_helper,
)


def prepare_skaffold_render_env() -> None:
    """Prepare the environment consumed by the generic Skaffold step."""
    env = skaffold_runtime_env(CONFIG)
    if "CILIUM_ENABLED" in CONFIG:
        env["CILIUM_ENABLED"] = (
            "true" if config_bool(CONFIG, "CILIUM_ENABLED", False) else "false"
        )
    else:
        env["CILIUM_ENABLED"] = "false"

    set_state_value(STATE, "skaffold_render_env", env)
    set_state_value(STATE, "cilium_skaffold_enabled", env.get("CILIUM_ENABLED") == "true")


def docker_login_registry(docker_env: dict[str, str]) -> None:
    """Authenticate Docker CLI to the local HTTPS registry when credentials exist."""
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
    """Start build helper and save Docker env overrides for the generic Skaffold build."""
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
    """Clear stale 5Gcore Helm releases left by interrupted deploys."""
    context = kube_context_name(CONFIG)
    clear_pending_helm_release(context, "csi-driver-smb", "kube-system", config=CONFIG)
    clear_pending_helm_release(context, "free5gc-helm", "free5gc", config=CONFIG)
    clear_pending_helm_release(context, "ueransim", "free5gc", config=CONFIG)
    set_state_value(STATE, "fivegcore_pre_deploy_cleanup", True)


def main() -> None:
    clear_helm_locks()
    prepare_skaffold_render_env()
    prepare_skaffold_build_env()
    log("5Gcore Skaffold build helper ready for HTTPS registry push.")


if __name__ == "__main__":
    main()
