#!/usr/bin/env python3
"""Provide small registry naming helpers.

The pipeline and hooks need a single way to derive the local registry endpoint
and image tag from CONFIG. This module keeps those conventions centralized so
Compose, Skaffold, attackers, and target hooks agree on names."""

from __future__ import annotations

from typing import Any, Mapping

from .config import config_str


def registry_endpoint(config: Mapping[str, Any], *, default_name: str = "registry", default_port: int = 5000) -> str:
    """Return the registry host:port used by Kubernetes images."""
    return f"{config_str(config, 'REGISTRY_NAME', default_name)}:{config_str(config, 'REGISTRY_PORT', default_port)}"


def image_version(config: Mapping[str, Any], *, default: str = "2.0.2") -> str:
    """Return the lab image version tag."""
    return config_str(config, "IMAGE_VERSION", default)
