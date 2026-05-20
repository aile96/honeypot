#!/usr/bin/env python3
"""Prepare the OpenTelemetry Skaffold render and deploy environment."""

from lib import (
    clear_pending_helm_release,
    config_bool,
    config_str,
    kube_context_name,
    log,
    python_value_to_env,
    set_state_value,
    skaffold_runtime_env,
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


if __name__ == "__main__":
    main()
