#!/usr/bin/env python3
"""Render and deploy the Docker Compose underlay with prebuilt images."""

from pathlib import Path

from lib import (
    compose_down,
    compose_environment,
    compose_file_path,
    compose_up,
    config_int,
    configured_compose_services,
    discover_compose_services,
    generated_dir,
    log,
    set_state_value,
    substitute_vars,
    target_conf_file,
)


def render_compose() -> Path:
    template = target_conf_file(CONFIG, "compose.yaml.tmpl")
    if not template.is_file():
        raise SystemExit(f"Compose template not found: {template}")
    out = generated_dir(CONFIG) / "compose.yaml"
    out.parent.mkdir(parents=True, exist_ok=True)
    rendered = substitute_vars(template.read_text(encoding="utf-8"), compose_environment(CONFIG))
    if not rendered.endswith("\n"):
        rendered += "\n"
    out.write_text(rendered, encoding="utf-8")
    CONFIG["COMPOSE_FILE"] = str(out)
    set_state_value(STATE, "compose_template", str(template))
    set_state_value(STATE, "compose_file", str(out))
    return out


def compose_run_services(env: dict[str, str]) -> list[str]:
    configured = configured_compose_services(CONFIG, "COMPOSE_DEPLOY_SERVICES", [])
    if configured:
        return configured
    configured = configured_compose_services(CONFIG, "COMPOSE_SERVICES", [])
    if configured:
        return configured
    return discover_compose_services(CONFIG, env=env)


def main() -> None:
    compose_file = render_compose()
    env = compose_environment(CONFIG)
    lock = generated_dir(CONFIG) / "cache-copy.lock"
    lock.write_text("locked\n", encoding="utf-8")
    try:
        log("Removing stale Compose stack before deployment.")
        compose_down(CONFIG, env=env)
        services = compose_run_services(env)
        timeout = config_int(CONFIG, "UNDERLAY_COMPOSE_WAIT_TIMEOUT_SECONDS", 120, minimum=1)
        compose_up(CONFIG, services, wait_timeout_seconds=timeout, env=env)
    finally:
        lock.unlink(missing_ok=True)

    set_state_value(STATE, "compose_cache_lock", str(lock))
    set_state_value(STATE, "underlay_compose_file", str(compose_file))
    set_state_value(STATE, "underlay_services_started", bool(services))
    set_state_value(STATE, "underlay_services", services)
    log("Compose deploy step completed.")


if __name__ == "__main__":
    main()
