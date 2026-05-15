#!/usr/bin/env python3
"""Build and start the target Docker Compose underlay.

The step is intentionally generic: target hooks decide which Compose services are
built and started, while this file only invokes Docker Compose and waits for the
selected services to become running or healthy. Registry setup, TLS material, and
other target-specific preparation belong in hooks."""

from lib import (
    compose_build,
    compose_environment,
    compose_file_path,
    compose_up,
    config_bool,
    config_int,
    configured_compose_services,
    discover_compose_services,
    log,
    set_state_value,
)


def compose_env() -> dict[str, str]:
    """Return the environment already prepared by hooks."""
    return compose_environment(CONFIG)


def build_compose_images(env: dict[str, str]) -> list[str]:
    """Build configured Compose images."""
    services = configured_compose_services(CONFIG, "COMPOSE_BUILD_SERVICES", [])
    timeout = config_int(CONFIG, "DOCKER_BUILD_TIMEOUT_SECONDS", 600, minimum=0) or None

    compose_build(
        CONFIG,
        services,
        timeout_seconds=timeout,
        env=env,
    )

    return services


def compose_run_services(env: dict[str, str]) -> list[str]:
    """Return services selected for Compose deployment."""
    configured = configured_compose_services(CONFIG, "COMPOSE_DEPLOY_SERVICES", [])
    if configured:
        return configured

    configured = configured_compose_services(CONFIG, "COMPOSE_SERVICES", [])
    if configured:
        return configured

    return discover_compose_services(CONFIG, env=env)


def start_compose_underlay(env: dict[str, str]) -> list[str]:
    """Start Compose services and wait for readiness."""
    services = compose_run_services(env)
    timeout = config_int(CONFIG, "UNDERLAY_COMPOSE_WAIT_TIMEOUT_SECONDS", 120, minimum=1)

    compose_up(
        CONFIG,
        services,
        wait_timeout_seconds=timeout,
        env=env,
    )

    return services


def main() -> None:
    compose_file = compose_file_path(CONFIG)
    if not compose_file.is_file():
        raise SystemExit(f"Target Compose file not found: {compose_file}")

    env = compose_env()

    compose_build_enabled = config_bool(CONFIG, "COMPOSE_BUILD_ENABLED", True)
    if compose_build_enabled:
        built_services = build_compose_images(env)
    else:
        log("Skipping Compose image build because COMPOSE_BUILD_ENABLED=false.")
        built_services = []

    services = start_compose_underlay(env)

    set_state_value(STATE, "compose_images_built", built_services)
    set_state_value(STATE, "compose_build_enabled", compose_build_enabled)
    set_state_value(STATE, "underlay_compose_file", str(compose_file))
    set_state_value(STATE, "underlay_services_started", bool(services))
    set_state_value(STATE, "underlay_services", services)

    log("Compose build and underlay startup completed.")


if __name__ == "__main__":
    main()
