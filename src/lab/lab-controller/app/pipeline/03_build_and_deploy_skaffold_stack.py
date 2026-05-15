#!/usr/bin/env python3
"""Render, build, and deploy the target Skaffold stack.

This step renders skaffold.yaml.tmpl with CONFIG and STATE values, optionally lists
artifacts for debugging, and runs Skaffold against the active Kind context. Hooks
own all target-specific preparation such as registry login, build-helper startup,
and stale Helm release cleanup."""

import json
import re
from pathlib import Path

from lib import (
    config_int,
    get_state_value,
    kube_context_name,
    log,
    render_skaffold_config,
    run_cmd,
    skaffold_artifacts_path,
    skaffold_workdir,
    set_state_value,
)



def skaffold_has_build_artifacts(skaffold_file: Path) -> bool:
    """Return True when the rendered Skaffold file declares build artifacts."""
    text = skaffold_file.read_text(encoding="utf-8")
    return (
        re.search(r"(?m)^build:\s*$", text) is not None
        and re.search(r"(?m)^\s*artifacts:\s*$", text) is not None
        and re.search(r"(?m)^\s*-\s*image:", text) is not None
    )


def skaffold_has_deploy_config(skaffold_file: Path) -> bool:
    """Return True when the rendered Skaffold file declares a deploy section."""
    text = skaffold_file.read_text(encoding="utf-8")
    return re.search(r"(?m)^deploy:\s*$", text) is not None

def skaffold_cli_config_path(skaffold_file: Path, workdir: Path) -> str:
    try:
        return str(skaffold_file.relative_to(workdir))
    except ValueError:
        return str(skaffold_file)


def skaffold_env_from_hook() -> dict[str, str]:
    """Return Skaffold env prepared by HOOK_PRE_03."""
    env = get_state_value(STATE, "skaffold_render_env", None)

    if not isinstance(env, dict):
        raise SystemExit("Missing STATE['skaffold_render_env']; prepare it in HOOK_PRE_03.py.")

    return {str(key): str(value) for key, value in env.items()}


def skaffold_build_env(skaffold_env: dict[str, str]) -> dict[str, str]:
    """Return build env prepared by HOOK_PRE_03."""
    env = dict(skaffold_env)

    overrides = get_state_value(STATE, "skaffold_build_env_overrides", {})
    if isinstance(overrides, dict):
        env.update({str(key): str(value) for key, value in overrides.items()})

    return env


def render_skaffold(skaffold_env: dict[str, str]) -> Path:
    """Render Skaffold config."""
    skaffold_file, skaffold_template = render_skaffold_config(CONFIG, skaffold_env)

    set_state_value(STATE, "skaffold_config", str(skaffold_file))
    if skaffold_template is not None:
        set_state_value(STATE, "skaffold_template_config", str(skaffold_template))

    return skaffold_file


def build_skaffold(skaffold_file: Path, skaffold_env: dict[str, str]) -> list[str]:
    """Run Skaffold build with Skaffold artifact cache always disabled."""
    artifact_file = skaffold_artifacts_path(CONFIG)
    artifact_file.unlink(missing_ok=True)

    if not skaffold_has_build_artifacts(skaffold_file):
        log("Skipping Skaffold build because no build artifacts are declared.")
        set_state_value(STATE, "skaffold_build_artifacts", "")
        set_state_value(STATE, "cluster_images", [])
        set_state_value(STATE, "pushed_cluster_images", [])
        return []

    timeout = config_int(CONFIG, "DOCKER_BUILD_TIMEOUT_SECONDS", 600, minimum=0) or None
    workdir = skaffold_workdir(CONFIG)
    build_env = skaffold_build_env(skaffold_env)

    run_cmd(
        [
            "skaffold",
            "build",
            "-f",
            skaffold_cli_config_path(skaffold_file, workdir),
            "--cache-artifacts=false",
            "--file-output",
            str(artifact_file),
        ],
        timeout_seconds=timeout,
        config=CONFIG,
        env=build_env,
        cwd=workdir,
    )

    images: list[str] = []

    if artifact_file.is_file():
        data = json.loads(artifact_file.read_text(encoding="utf-8"))
        builds = data.get("builds", []) if isinstance(data, dict) else []
        images = [
            str(item.get("tag"))
            for item in builds
            if isinstance(item, dict) and item.get("tag")
        ]

    set_state_value(STATE, "skaffold_build_artifacts", str(artifact_file))
    set_state_value(STATE, "cluster_images", images)
    set_state_value(STATE, "pushed_cluster_images", images)

    return images


def deploy_skaffold(skaffold_file: Path, skaffold_env: dict[str, str]) -> None:
    """Run Skaffold deploy."""
    if not skaffold_has_deploy_config(skaffold_file):
        log("Skipping Skaffold deploy because no deploy section is declared.")
        return

    workdir = skaffold_workdir(CONFIG)

    command = [
        "skaffold",
        "deploy",
        "-f",
        skaffold_cli_config_path(skaffold_file, workdir),
        "--kube-context",
        kube_context_name(CONFIG),
    ]

    artifact_file = skaffold_artifacts_path(CONFIG)
    if artifact_file.is_file():
        command.extend(["--build-artifacts", str(artifact_file)])

    run_cmd(
        command,
        config=CONFIG,
        env=skaffold_env,
        cwd=workdir,
    )

    set_state_value(STATE, "skaffold_config", str(skaffold_file))
    if artifact_file.is_file():
        set_state_value(STATE, "skaffold_build_artifacts", str(artifact_file))


def main() -> None:
    skaffold_env = skaffold_env_from_hook()

    skaffold_file = render_skaffold(skaffold_env)
    build_skaffold(skaffold_file, skaffold_env)
    deploy_skaffold(skaffold_file, skaffold_env)

    set_state_value(STATE, "skaffold_build_detected", skaffold_has_build_artifacts(skaffold_file))
    set_state_value(STATE, "skaffold_deploy_detected", skaffold_has_deploy_config(skaffold_file))

    log("Skaffold render, build, and deploy completed.")


if __name__ == "__main__":
    main()