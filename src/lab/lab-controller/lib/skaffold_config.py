#!/usr/bin/env python3
"""Render and describe the target Skaffold configuration.

This module resolves the Skaffold working directory, renders skaffold.yaml.tmpl
with CONFIG/STATE values, exposes render-time environment, and extracts artifact
information for debugging before the generic Skaffold step runs."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from .config import config_str, config_to_env
from .target import generated_dir, target_conf_file, target_root
from .utils import substitute_vars


def skaffold_workdir(config: Mapping[str, Any]) -> Path:
    """Return the working directory from which Skaffold must run."""
    return target_root(config)


def skaffold_config_path(config: Mapping[str, Any]) -> Path:
    """Return the rendered Skaffold config path."""
    return skaffold_rendered_config_path(config)


def skaffold_template_path(config: Mapping[str, Any]) -> Path:
    """Return the target-managed Skaffold template path."""
    return target_conf_file(config, "skaffold.yaml.tmpl")


def skaffold_rendered_config_path(config: Mapping[str, Any]) -> Path:
    """Return the rendered Skaffold config path."""
    return generated_dir(config) / "skaffold.yaml"


def render_skaffold_config(config: Mapping[str, Any], env: Mapping[str, str] | None = None) -> tuple[Path, Path | None]:
    """Render Skaffold from the target-managed template."""
    template = skaffold_template_path(config)
    if not template.is_file():
        raise SystemExit(f"Target Skaffold template not found: {template}")

    out = skaffold_rendered_config_path(config)
    out.parent.mkdir(parents=True, exist_ok=True)
    rendered = substitute_vars(template.read_text(encoding="utf-8"), env or skaffold_runtime_env(config))
    if not rendered.endswith("\n"):
        rendered += "\n"
    out.write_text(rendered, encoding="utf-8")
    return out, template


def skaffold_artifacts_path(config: Mapping[str, Any]) -> Path:
    """Return the generated Skaffold build-artifacts path."""
    return generated_dir(config) / "skaffold-build-artifacts.json"


def skaffold_runtime_env(config: Mapping[str, Any]) -> dict[str, str]:
    """Return the environment exposed to Skaffold."""
    env = config_to_env(config)

    kube_apiserver_ips = config_str(config, "KUBE_APISERVER_IPS", "auto").strip()
    env["KUBE_APISERVER_IPS"] = "" if kube_apiserver_ips in {"", "auto", "[]"} else kube_apiserver_ips.replace(",", r"\,")
    return env
