#!/usr/bin/env python3
"""Render, build, and deploy the target Skaffold stack."""

import json
import re
import shutil
from pathlib import Path

from lib import (
    config_str,
    docker_login_internal_registry,
    get_state_value,
    generated_dir,
    kube_context_name,
    log,
    registry_endpoint,
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
    """Return Skaffold env prepared by HOOK_PRE_04."""
    env = get_state_value(STATE, "skaffold_render_env", None)

    if not isinstance(env, dict):
        raise SystemExit("Missing STATE['skaffold_render_env']; prepare it in HOOK_PRE_04.py.")

    return {str(key): str(value) for key, value in env.items()}


def prepare_writable_helm_charts(config: dict, env: dict[str, str]) -> Path | None:
    """Copy target Helm charts to runtime so Skaffold/Helm can write temp files."""
    source = Path(str(config.get("HELM_CHARTS_ROOT") or skaffold_workdir(config) / "helm-charts"))
    if not source.is_dir():
        return None

    destination = generated_dir(config) / "helm-charts"
    if source.resolve() != destination.resolve():
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            shutil.rmtree(destination)
        shutil.copytree(
            source,
            destination,
            symlinks=True,
            ignore=shutil.ignore_patterns("tmpcharts-*", "__pycache__"),
        )

    config["HELM_CHARTS_ROOT"] = str(destination)
    env["HELM_CHARTS_ROOT"] = str(destination)
    log(f"Prepared writable Helm charts at {destination}.")
    return destination


def render_skaffold(skaffold_env: dict[str, str]) -> Path:
    """Render Skaffold config."""
    skaffold_file, skaffold_template = render_skaffold_config(CONFIG, skaffold_env)

    set_state_value(STATE, "skaffold_config", str(skaffold_file))
    if skaffold_template is not None:
        set_state_value(STATE, "skaffold_template_config", str(skaffold_template))

    return skaffold_file


def built_images_from_artifacts(artifact_file: Path) -> list[str]:
    """Return image tags emitted by `skaffold build --file-output`."""
    if not artifact_file.is_file():
        return []
    try:
        data = json.loads(artifact_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    builds = data.get("builds", [])
    if not isinstance(builds, list):
        return []
    images: list[str] = []
    for item in builds:
        if isinstance(item, dict) and item.get("tag"):
            images.append(str(item["tag"]))
    return images


def skaffold_insecure_registry_args(config: dict) -> list[str]:
    """Return CLI flags for every HTTP registry Skaffold must contact."""
    endpoints = [
        registry_endpoint(config),
        config_str(config, "REGISTRY_CACHE_ENDPOINT", "registry-lab:5000", allow_empty=False),
    ]
    args: list[str] = []
    seen: set[str] = set()
    for endpoint in endpoints:
        endpoint = endpoint.strip()
        if endpoint and endpoint not in seen:
            args.extend(["--insecure-registry", endpoint])
            seen.add(endpoint)
    return args


def build_skaffold(skaffold_file: Path, skaffold_env: dict[str, str]) -> list[str]:
    """Run Skaffold build using the rendered Skaffold config."""
    artifact_file = skaffold_artifacts_path(CONFIG)
    artifact_file.unlink(missing_ok=True)

    if not skaffold_has_build_artifacts(skaffold_file):
        log("Skipping Skaffold build because no build artifacts are declared.")
        set_state_value(STATE, "skaffold_build_artifacts", "")
        set_state_value(STATE, "cluster_images", [])
        set_state_value(STATE, "pushed_cluster_images", [])
        return []

    docker_login_internal_registry(CONFIG)
    workdir = skaffold_workdir(CONFIG)
    command = [
        "skaffold",
        "build",
        "-f",
        skaffold_cli_config_path(skaffold_file, workdir),
        "--file-output",
        str(artifact_file),
        *skaffold_insecure_registry_args(CONFIG),
    ]

    run_cmd(command, config=CONFIG, env=skaffold_env, cwd=workdir)
    images = built_images_from_artifacts(artifact_file)

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
    writable_helm_charts = prepare_writable_helm_charts(CONFIG, skaffold_env)
    if writable_helm_charts is not None:
        set_state_value(STATE, "helm_charts_root", str(writable_helm_charts))

    skaffold_file = render_skaffold(skaffold_env)
    build_skaffold(skaffold_file, skaffold_env)
    deploy_skaffold(skaffold_file, skaffold_env)

    set_state_value(STATE, "skaffold_build_detected", skaffold_has_build_artifacts(skaffold_file))
    set_state_value(STATE, "skaffold_deploy_detected", skaffold_has_deploy_config(skaffold_file))

    log("Skaffold render, build, and deploy completed.")


if __name__ == "__main__":
    main()
