#!/usr/bin/env python3
"""Prepare the 5Gcore Skaffold render and deploy environment."""

from lib import (
    clear_pending_helm_release,
    config_bool,
    config_str,
    kube_context_name,
    log,
    python_value_to_env,
    skaffold_runtime_env,
    set_state_value,
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
    """Prepare the environment consumed by the generic Skaffold step."""
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
    log("5Gcore Skaffold render environment ready.")


if __name__ == "__main__":
    main()
