#!/usr/bin/env python3
"""Build-image manifest helpers for cache and deploy registries."""

from __future__ import annotations

import base64
import tomllib
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Mapping

from .command import CommandError, run_cmd
from .config import Config, config_bool, config_int, config_str, config_to_env
from .logging import log, warn
from .registry import image_version, registry_endpoint
from .target import target_conf_file, target_root
from .utils import substitute_vars


def images_config_path(config: Mapping[str, Any]) -> Path:
    return target_conf_file(config, "images.toml")


def common_root(config: Mapping[str, Any]) -> Path:
    return Path(config_str(config, "COMMON_CODE_ROOT", "/workdir/common"))


def load_image_definitions(config: Mapping[str, Any]) -> list[dict[str, Any]]:
    path = images_config_path(config)
    if not path.is_file():
        raise SystemExit(f"Image definition file not found: {path}")
    with path.open("rb") as handle:
        data = tomllib.load(handle)
    images = data.get("images", [])
    if not isinstance(images, list):
        raise SystemExit(f"{path}: [[images]] must be a list")
    result: list[dict[str, Any]] = []
    for item in images:
        if not isinstance(item, dict):
            raise SystemExit(f"{path}: invalid image entry {item!r}")
        if not str(item.get("name", "")).strip():
            raise SystemExit(f"{path}: image entry missing name: {item!r}")
        has_context = bool(str(item.get("context", "")).strip())
        has_source = bool(str(item.get("source", "")).strip())
        if has_context and has_source:
            raise SystemExit(f"{path}: image entry cannot define both context and source: {item!r}")
        if not has_context and not has_source:
            raise SystemExit(f"{path}: image entry missing context or source: {item!r}")
        item.setdefault("dockerfile", "Dockerfile")
        item.setdefault("tag", "${IMAGE_VERSION}")
        item.setdefault("build_args", {})
        item.setdefault("phase_build_args", {})
        item.setdefault("depends", [])
        item.setdefault("validators", [])
        result.append(item)
    return result


def render_image_value(value: Any, config: Mapping[str, Any]) -> str:
    return substitute_vars(str(value), config_to_env(config, base_env={}))



def resolve_context(config: Mapping[str, Any], image: Mapping[str, Any]) -> Path:
    raw = render_image_value(image["context"], config)
    path = Path(raw)
    if path.is_absolute():
        return path
    return (target_root(config) / path).resolve()


def resolve_dockerfile(config: Mapping[str, Any], image: Mapping[str, Any], context: Path | None = None) -> Path:
    raw = render_image_value(image.get("dockerfile", "Dockerfile"), config)
    path = Path(raw)
    if path.is_absolute():
        return path
    base = context if context is not None else resolve_context(config, image)
    return (base / path).resolve()


def cache_registry_endpoint(config: Mapping[str, Any]) -> str:
    return config_str(config, "REGISTRY_CACHE_ENDPOINT", "registry-lab:5000", allow_empty=False)


def cache_image_ref(config: Mapping[str, Any], image: Mapping[str, Any]) -> str:
    name = render_image_value(image["name"], config)
    tag = render_image_value(image.get("tag", "${IMAGE_VERSION}"), config) or image_version(config)
    return f"{cache_registry_endpoint(config)}/{name}:{tag}"


def registry_has_image(ref: str) -> bool:
    try:
        registry, rest = ref.split("/", 1)
        name, tag = rest.rsplit(":", 1)
    except ValueError:
        return False
    url = f"http://{registry}/v2/{name}/manifests/{tag}"
    request = urllib.request.Request(url, headers={"Accept": "application/vnd.docker.distribution.manifest.v2+json"})
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return 200 <= response.status < 300
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def build_args(config: Mapping[str, Any], image: Mapping[str, Any], *, phase: str | None = None) -> dict[str, str]:
    args: dict[str, str] = {}
    common = image.get("build_args", {})
    if isinstance(common, dict):
        args.update({str(key): render_image_value(value, config) for key, value in common.items()})
    phase_args = image.get("phase_build_args", {})
    if phase and isinstance(phase_args, dict):
        selected = phase_args.get(phase, {})
        if isinstance(selected, dict):
            args.update({str(key): render_image_value(value, config) for key, value in selected.items()})
    return args


def docker_login_internal_registry(config: Config) -> None:
    username = config_str(config, "REGISTRY_USER", "", allow_empty=True).strip()
    password = config_str(config, "REGISTRY_PASS", "", allow_empty=True).strip()
    if not username:
        return
    run_cmd(
        ["docker", "login", registry_endpoint(config), "-u", username, "--password-stdin"],
        input_text=f"{password}\n",
        config=config,
    )


def docker_build(
    ref: str,
    context: Path,
    dockerfile: Path,
    args: Mapping[str, str],
    config: Config,
    *,
    buildkit: bool = False,
    network: str | None = None,
) -> None:
    command = ["docker", "build", "--pull=false", "--no-cache"]
    if network is None and not buildkit:
        network = config_str(config, "CP_NETWORK", "lab")
    if network:
        command.extend(["--network", network])
    command.extend(["-t", ref])
    command.extend(["-f", str(dockerfile)])
    for key, value in args.items():
        command.extend(["--build-arg", f"{key}={value}"])
    command.append(str(context))
    env = config_to_env(config)
    env["DOCKER_BUILDKIT"] = "1" if buildkit else "0"
    run_cmd(command, timeout_seconds=config_int(config, "DOCKER_BUILD_TIMEOUT_SECONDS", 0, minimum=0) or None, env=env)


def docker_pull(ref: str, config: Config) -> None:
    run_cmd(["docker", "pull", ref], config=config)


def docker_tag(source: str, target: str, config: Config) -> None:
    run_cmd(["docker", "tag", source, target], config=config)


def docker_push(ref: str, config: Config) -> None:
    run_cmd(["docker", "push", ref], config=config)


def image_validators(image: Mapping[str, Any]) -> list[str]:
    validators = image.get("validators", [])
    if isinstance(validators, str):
        return [validators]
    if isinstance(validators, list):
        return [str(item) for item in validators]
    raise SystemExit(f"Invalid validators value for image {image.get('name')!r}: {validators!r}")


def validate_javaagent_image(ref: str, config: Config) -> bool:
    try:
        completed = run_cmd(
            [
                "docker",
                "run",
                "--rm",
                "--user",
                "1000",
                "--entrypoint",
                "java",
                "-e",
                "OTEL_SDK_DISABLED=true",
                ref,
                "-version",
            ],
            check=False,
            capture_output=True,
            timeout_seconds=60,
            raise_on_error=True,
            config=config,
        )
    except CommandError as exc:
        warn(f"Image validation failed for {ref}: {exc}")
        return False

    output = f"{completed.stdout or ''}\n{completed.stderr or ''}"
    if completed.returncode != 0:
        warn(f"Image validation failed for {ref}: java exited with code {completed.returncode}")
        return False
    if "opentelemetry-javaagent" not in output:
        warn(f"Image validation failed for {ref}: OpenTelemetry Java agent did not load")
        return False
    return True


def validate_cache_image(ref: str, image: Mapping[str, Any], config: Config) -> bool:
    for validator in image_validators(image):
        if validator == "javaagent":
            if not validate_javaagent_image(ref, config):
                return False
            continue
        raise SystemExit(f"Unknown image validator for {ref}: {validator}")
    return True


def build_cache_image(config: Config, image: Mapping[str, Any]) -> str:
    ref = cache_image_ref(config, image)
    force = config_bool(config, "BUILD_CONTAINERS", False)
    if not force and registry_has_image(ref):
        if validate_cache_image(ref, image, config):
            log(f"Image already present in cache registry, skipping build and push: {ref}")
            return ""
        warn(f"Image already present in cache registry but failed validation; rebuilding: {ref}")

    source = str(image.get("source", "")).strip()
    if source:
        source_ref = render_image_value(source, config)
        log(f"Pulling remote image for cache: {source_ref} -> {ref}")
        docker_pull(source_ref, config)
        docker_tag(source_ref, ref, config)
    else:
        context = resolve_context(config, image)
        dockerfile = resolve_dockerfile(config, image, context)
        docker_build(ref, context, dockerfile, build_args(config, image), config, buildkit=True, network="")

    if not validate_cache_image(ref, image, config):
        raise SystemExit(f"Built cache image failed validation: {ref}")
    return ref


def push_cache_image(config: Config, ref: str) -> str:
    if not ref:
        return ""
    docker_push(ref, config)
    return ref


def parallel_map(label: str, workers: int, items: list[Any], fn) -> list[Any]:
    if not items:
        return []
    results: list[Any] = []
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {pool.submit(fn, item): item for item in items}
        for future in as_completed(futures):
            item = futures[future]
            try:
                results.append(future.result())
            except Exception as exc:
                raise SystemExit(f"{label} failed for {item!r}: {exc}") from exc
    return results
