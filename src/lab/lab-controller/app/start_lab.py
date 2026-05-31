#!/usr/bin/env python3
"""Run the CONFIG/STATE-driven lab pipeline inside the controller container.

This runner loads the persisted runtime CONFIG written by start.py from the fixed
/runtime/config.toml mount, saves CONFIG back to disk, and executes each pipeline
step with matching PRE/POST hooks. CONFIG stores desired configuration, while
STATE stores runtime discoveries and resumable progress."""

from __future__ import annotations

import os
import runpy
import sys
import time
import traceback
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from lib import (
    Config,
    State,
    config_to_env,
    die,
    discover_pipeline_scripts,
    err,
    load_or_create_state,
    load_runtime_config,
    log,
    mark_pipeline_failed,
    mark_pipeline_ready,
    parse_bool_value,
    record_step_finish,
    record_step_start,
    record_unit_finish,
    record_unit_start,
    require_config,
    require_port_config,
    resolve_hook_candidates,
    resolve_step_retry_policy,
    save_config_file,
    save_state_file,
    set_config_default,
    state_values,
    timestamp,
    unit_completed,
    validate_resume_state,
    warn,
)

SCRIPT_START_EPOCH = int(time.time())

DEFAULT_ENV_FILE = "/runtime/config.toml"
DEFAULT_CODE_ROOT = "/workdir/code"
DEFAULT_RES_DIR = "/res"
DEFAULT_PIPELINE_ROOT = "/app/pipeline"

_MISSING = object()


# Dockerfile ENV values that are still meaningful to the pipeline runner.
DOCKERFILE_PIPELINE_ENV_KEYS = (
    "LAB_NAME",
    "CLUSTER_PROFILE",
    "ENV_FILE",
    "RES_DIR",
    "RUNTIME_DIR",
    "RESULTS_DIR",
    "GENERATED_DIR",
    "STATE_FILE",
    "COMPOSE_PROJECT_NAME",
    "CP_NETWORK",
    "KUBE_CONTEXT",
    "CONTROLLER_PROXY_CONTAINER_PORT",
    "EXPOSE_TO_HOST",
    "HOST_SOCKET",
    "PROXY_BIND_ALL",
)


# Environment values passed by start.py through docker run -e and still useful to the pipeline.
START_PY_PIPELINE_ENV_KEYS = (
    "CODE_ROOT",
    "COMMON_CODE_ROOT",
    "CONFIG_FILE",
    "ENV_FILE",
    "LAB_NAME",
    "CLUSTER_PROFILE",
    "RES_DIR",
    "RUNTIME_DIR",
    "RESULTS_DIR",
    "GENERATED_DIR",
    "STATE_FILE",
    "COMPOSE_PROJECT_NAME",
    "CP_NETWORK",
    "KUBE_CONTEXT",
    "CONTROLLER_PROXY_CONTAINER_PORT",
    "EXPOSE_TO_HOST",
    "HOST_SOCKET",
    "PROXY_BIND_ALL",
    "HOST_CODE_ROOT",
    "HOST_RES_DIR",
    "HOST_RUNTIME_DIR",
    "HOST_RESULTS_DIR",
)


PIPELINE_ALLOWED_ENV_KEYS = tuple(
    dict.fromkeys((*DOCKERFILE_PIPELINE_ENV_KEYS, *START_PY_PIPELINE_ENV_KEYS))
)


PIPELINE_REQUIRED_CONFIG_KEYS = (
    "CODE_ROOT",
    "ENV_FILE",
    "RES_DIR",
    "LAB_NAME",
    "EXPOSE_TO_HOST",
    "HOST_CODE_ROOT",
    "HOST_RES_DIR",
    "HOST_RUNTIME_DIR",
    "HOST_RESULTS_DIR",
)


PIPELINE_BOOL_CONFIG_KEYS = (
    "EXPOSE_TO_HOST",
)


PIPELINE_OPTIONAL_BOOL_CONFIG_KEYS = (
    "PROXY_BIND_ALL",
)


PIPELINE_ABSOLUTE_PATH_KEYS = (
    "CODE_ROOT",
    "CONFIG_FILE",
    "ENV_FILE",
    "RES_DIR",
    "HOST_CODE_ROOT",
    "HOST_RES_DIR",
    "HOST_RUNTIME_DIR",
    "HOST_RESULTS_DIR",
    "PIPELINE_ROOT",
    "HOOKS_DIR",
    "CONTROLLER_HOOKS_DIR",
    "CONTAINERS_ROOT",
    "CALDERA_ROOT",
    "CALDERA_ADVERSARIES_DIR",
    "HELM_CHARTS_ROOT",
    "RESULTS_DIR",
    "STATE_FILE",
    "STATE_DIR",
    "RUNTIME_DIR",
    "GENERATED_DIR",
)


@contextmanager
def scoped_env(updates: dict[str, str]):
    """Temporarily expose stringified CONFIG values via os.environ."""
    previous: dict[str, str | object] = {}

    for name, value in updates.items():
        previous[name] = os.environ.get(name, _MISSING)
        os.environ[name] = value

    try:
        yield
    finally:
        for name, value in previous.items():
            if value is _MISSING:
                os.environ.pop(name, None)
            else:
                os.environ[name] = str(value)


def run_python_file_once(
    script_path: str | Path,
    *,
    init_globals: dict[str, Any],
) -> int:
    """Execute a Python script as __main__ and return its exit code."""
    path = Path(script_path)

    try:
        runpy.run_path(
            str(path),
            run_name="__main__",
            init_globals=init_globals,
        )
        return 0

    except SystemExit as exc:
        if isinstance(exc.code, int):
            return exc.code
        return 0 if exc.code in (None, "") else 1

    except Exception:
        traceback.print_exc()
        return 1


def step_globals(
    *,
    config: Config,
    state: State,
    step_path: Path,
) -> dict[str, Any]:
    """Build globals injected into a pipeline step."""
    return {
        "CONFIG": config,
        "STATE": state,
        "STEP_SCRIPT": str(step_path),
        "STEP_NAME": step_path.name,
        "STEP_STEM": step_path.stem,
    }


def hook_globals(
    *,
    config: Config,
    state: State,
    step_path: Path,
    hook_path: Path,
    hook_kind: str,
) -> dict[str, Any]:
    """Build globals injected into a pipeline hook."""
    return {
        "CONFIG": config,
        "STATE": state,
        "HOOK_KIND": hook_kind,
        "HOOK_SCRIPT": str(hook_path),
        "HOOK_NAME": hook_path.name,
        "HOOK_STEP_SCRIPT": str(step_path),
        "HOOK_STEP_NAME": step_path.name,
        "HOOK_STEP_STEM": step_path.stem,
    }


def step_unit_id(step_path: Path) -> str:
    """Return the stable STATE id for a pipeline step."""
    return f"step:{step_path.name}"


def hook_unit_id(kind: str, step_path: Path, hook_path: Path) -> str:
    """Return the stable STATE id for a pipeline hook."""
    return f"hook:{kind.upper()}:{step_path.name}:{hook_path.name}"


def run_unit_once(
    *,
    script_path: Path,
    config: Config,
    state: State,
    unit_id: str,
    unit_type: str,
    name: str,
    step_name: str | None,
    init_globals: dict[str, Any],
) -> int:
    """Run one resumable pipeline unit once and record it in STATE."""
    record_unit_start(
        state,
        unit_id,
        unit_type=unit_type,
        name=name,
        step_name=step_name,
    )

    if unit_type == "step":
        record_step_start(state, name)

    save_state_file(state)

    rc = run_python_file_once(script_path, init_globals=init_globals)
    save_config_snapshot(config)

    if unit_type == "step":
        record_step_finish(state, name, exit_code=rc)

    record_unit_finish(
        state,
        unit_id,
        unit_type=unit_type,
        name=name,
        step_name=step_name,
        exit_code=rc,
    )
    save_state_file(state)
    return rc


def save_config_snapshot(config: Config) -> None:
    """Persist CONFIG mutations made by a step or hook."""
    config_path = str(config.get("CONFIG_FILE") or config.get("ENV_FILE") or DEFAULT_ENV_FILE)
    save_config_file(config, config_path)


def run_unit_with_retry(
    *,
    script_path: Path,
    retry_policy_source: Path,
    config: Config,
    state: State,
    unit_id: str,
    unit_type: str,
    name: str,
    step_name: str | None,
    init_globals: dict[str, Any],
) -> bool:
    """Run a step or hook with numbered-step retry support and resume skipping."""
    if unit_completed(state, unit_id):
        log(f"Skipping completed {unit_type}: {name}")
        return True

    retries, delay = resolve_step_retry_policy(retry_policy_source, config)
    total_runs = retries + 1
    log(
        f"Retry policy for {unit_type} {name}: "
        f"step={Path(retry_policy_source).name}, retries={retries}, delay={delay}s."
    )

    last_rc = 0
    for attempt in range(1, total_runs + 1):
        last_rc = run_unit_once(
            script_path=script_path,
            config=config,
            state=state,
            unit_id=unit_id,
            unit_type=unit_type,
            name=name,
            step_name=step_name,
            init_globals=init_globals,
        )

        if last_rc == 0:
            return True

        if attempt < total_runs:
            warn(
                f"{unit_type.capitalize()} {name} failed with exit code {last_rc}. "
                f"Retrying attempt {attempt}/{retries} in {delay}s."
            )
            time.sleep(delay)

    message = f"{unit_type.capitalize()} {name} failed after {total_runs} run(s), last exit code={last_rc}."
    die(message)


def run_hooks(
    *,
    kind: str,
    step_script: str | Path,
    config: Config,
    state: State,
) -> None:
    """Run matching Python hooks for a step with retry and resume support.

    kind is usually PRE or POST. Missing hooks are silently skipped.
    """
    hooks_dir = config.get("HOOKS_DIR")
    if not hooks_dir:
        return

    step_path = Path(step_script)

    for hook_path in resolve_hook_candidates(hooks_dir, kind, step_path):
        if not hook_path.is_file():
            continue

        log(f"Running {kind.lower()} hook for {step_path.name}: {hook_path.name}")

        with scoped_env(config_to_env(config)):
            run_unit_with_retry(
                script_path=hook_path,
                retry_policy_source=step_path,
                config=config,
                state=state,
                unit_id=hook_unit_id(kind, step_path, hook_path),
                unit_type=f"hook:{kind.upper()}",
                name=hook_path.name,
                step_name=step_path.name,
                init_globals=hook_globals(
                    config=config,
                    state=state,
                    step_path=step_path,
                    hook_path=hook_path,
                    hook_kind=kind.upper(),
                ),
            )


def run_step_once(step_script: str | Path, config: Config, state: State) -> int:
    """Run one Python pipeline step once."""
    step_path = Path(step_script)

    if not step_path.is_file():
        die(f"Step script not found: {step_path}")

    if step_path.suffix != ".py":
        die(f"Unsupported step type: {step_path}. Only Python steps are supported.")

    log(f"Running step: {step_path.name}")
    return run_unit_once(
        script_path=step_path,
        config=config,
        state=state,
        unit_id=step_unit_id(step_path),
        unit_type="step",
        name=step_path.name,
        step_name=step_path.name,
        init_globals=step_globals(config=config, state=state, step_path=step_path),
    )


def run_step_with_retry(step_script: str | Path, config: Config, state: State) -> None:
    """Run one pipeline step with retry and resume support."""
    step_path = Path(step_script)

    run_unit_with_retry(
        script_path=step_path,
        retry_policy_source=step_path,
        config=config,
        state=state,
        unit_id=step_unit_id(step_path),
        unit_type="step",
        name=step_path.name,
        step_name=step_path.name,
        init_globals=step_globals(config=config, state=state, step_path=step_path),
    )



def pipeline_unit_order(config: Config, steps: list[Path]) -> tuple[list[str], dict[str, str]]:
    """Return the actual resumable unit order, including step hooks."""
    units: list[str] = []
    unit_to_step: dict[str, str] = {}
    hooks_dir = config.get("HOOKS_DIR")

    for step_path in steps:
        step_name = step_path.name

        if hooks_dir:
            for hook_path in resolve_hook_candidates(hooks_dir, "PRE", step_path):
                if not hook_path.is_file():
                    continue
                unit_id = hook_unit_id("PRE", step_path, hook_path)
                units.append(unit_id)
                unit_to_step[unit_id] = step_name

        unit_id = step_unit_id(step_path)
        units.append(unit_id)
        unit_to_step[unit_id] = step_name

        if hooks_dir:
            for hook_path in resolve_hook_candidates(hooks_dir, "POST", step_path):
                if not hook_path.is_file():
                    continue
                unit_id = hook_unit_id("POST", step_path, hook_path)
                units.append(unit_id)
                unit_to_step[unit_id] = step_name

    return units, unit_to_step

def discover_steps(config: Config) -> list[Path]:
    """Discover pipeline step scripts from PIPELINE_ROOT."""
    pipeline_root = Path(str(require_config(config, "PIPELINE_ROOT")))

    pipeline_root_str = str(pipeline_root)
    if pipeline_root_str not in sys.path:
        sys.path.insert(0, pipeline_root_str)

    steps = discover_pipeline_scripts(pipeline_root)

    if not steps:
        die(f"No Python pipeline scripts found in: {pipeline_root}")

    return steps


def overlay_allowed_environment(config: Config) -> None:
    """Overlay only Dockerfile/start.py env vars allowed for the pipeline."""
    for name in PIPELINE_ALLOWED_ENV_KEYS:
        value = os.environ.get(name)
        if value is not None and value != "":
            config[name] = value


def install_pipeline_defaults(config: Config) -> None:
    """Install defaults computed by the pipeline runner itself."""
    code_root = Path(str(config.get("CODE_ROOT", DEFAULT_CODE_ROOT)))
    set_config_default(config, "CODE_ROOT", str(code_root))

    lab_name = str(config.get("LAB_NAME") or config.get("CLUSTER_PROFILE") or "honeypotlab").strip()
    set_config_default(config, "LAB_NAME", lab_name)
    # Compatibility for target code that still reads CLUSTER_PROFILE.
    config["CLUSTER_PROFILE"] = lab_name
    set_config_default(config, "COMPOSE_PROJECT_NAME", f"honeypot-{lab_name}")
    set_config_default(config, "CP_NETWORK", "lab")
    set_config_default(config, "KUBE_CONTEXT", f"kind-{lab_name}")

    set_config_default(config, "PIPELINE_ROOT", DEFAULT_PIPELINE_ROOT)
    pipeline_root = Path(str(config["PIPELINE_ROOT"]))

    set_config_default(config, "HOOKS_DIR", str(code_root / "hooks" / "pipeline"))
    set_config_default(config, "CONTROLLER_HOOKS_DIR", str(code_root / "hooks" / "controller"))
    set_config_default(config, "CONTAINERS_ROOT", str(code_root / "containers"))
    set_config_default(config, "CALDERA_ROOT", str(code_root / "caldera"))
    set_config_default(config, "CALDERA_ADVERSARIES_DIR", str(code_root / "caldera" / "adversaries"))
    set_config_default(config, "HELM_CHARTS_ROOT", str(code_root / "helm-charts"))

    res_dir = str(config.get("RES_DIR", DEFAULT_RES_DIR))
    lab_name = str(config["LAB_NAME"])
    runtime_dir = Path(str(config.get("RUNTIME_DIR") or Path(res_dir) / "runtime" / lab_name))
    generated_dir = Path(str(config.get("GENERATED_DIR") or runtime_dir / "generated"))
    results_dir = Path(str(config.get("RESULTS_DIR") or Path(res_dir) / "results" / lab_name))

    set_config_default(config, "RES_DIR", res_dir)
    set_config_default(config, "RUNTIME_DIR", str(runtime_dir))
    set_config_default(config, "GENERATED_DIR", str(generated_dir))
    set_config_default(config, "RESULTS_DIR", str(results_dir))
    set_config_default(config, "STATE_FILE", str(config.get("STATE_FILE") or generated_dir / "lab-state.json"))
    set_config_default(config, "STATE_DIR", str(generated_dir))

    if str(config.get("REGISTRY_AUTH_DIR", "")).strip() in {"", "/res/runtime/registry"}:
        config["REGISTRY_AUTH_DIR"] = str(runtime_dir / "registry")
    if str(config.get("REGISTRY_CA_FILE", "")).strip() in {"", "/res/runtime/registry/certs/rootca.crt"}:
        config["REGISTRY_CA_FILE"] = str(runtime_dir / "registry" / "certs" / "rootca.crt")
    config.setdefault("COMPOSE_REGISTRY_AUTH_DIR", str(runtime_dir / "registry"))
    config.setdefault("COMPOSE_REGISTRY_CERTS_DIR", str(runtime_dir / "registry" / "certs"))
    config.setdefault("COMPOSE_REGISTRY_STORAGE_DIR", str(runtime_dir / "registry" / "storage"))
    config.setdefault("COMPOSE_ATTACKER_ENV_FILE", str(runtime_dir / "attacker" / "attacker.env"))
    config.setdefault("COMPOSE_ATTACKER_IPHOST_FILE", str(runtime_dir / "attacker" / "iphost"))
    config.setdefault("COMPOSE_ATTACKER_APISERVER_DIR", str(runtime_dir / "attacker" / "apiserver"))

    if not pipeline_root.is_absolute():
        die(f"PIPELINE_ROOT must be an absolute path, got: {pipeline_root}")


def validate_pipeline_config(config: Config) -> None:
    """Validate only configuration owned by the pipeline runner."""
    missing = [
        name
        for name in PIPELINE_REQUIRED_CONFIG_KEYS
        if name not in config or config[name] is None or str(config[name]).strip() == ""
    ]

    if missing:
        die("Missing required pipeline configuration value(s): " + ", ".join(missing))

    for name in PIPELINE_BOOL_CONFIG_KEYS:
        try:
            normalized = "true" if parse_bool_value(config[name], name=name) else "false"
        except ValueError as exc:
            die(str(exc))

        config[name] = normalized

    for name in PIPELINE_OPTIONAL_BOOL_CONFIG_KEYS:
        if name not in config or config[name] is None or str(config[name]).strip() == "":
            continue

        try:
            normalized = "true" if parse_bool_value(config[name], name=name) else "false"
        except ValueError as exc:
            die(str(exc))

        config[name] = normalized

    for name in PIPELINE_ABSOLUTE_PATH_KEYS:
        if name not in config or config[name] is None or str(config[name]).strip() == "":
            continue

        value = Path(str(config[name]))
        if not value.is_absolute():
            die(f"{name} must be an absolute path, got: {value}")

    code_root = Path(str(require_config(config, "CODE_ROOT")))
    if not code_root.is_dir():
        die(f"CODE_ROOT does not exist or is not a directory: {code_root}")

    env_file = Path(str(require_config(config, "ENV_FILE")))
    if not env_file.is_file():
        die(f"ENV_FILE does not exist or is not a file: {env_file}")

    pipeline_root = Path(str(require_config(config, "PIPELINE_ROOT")))
    if not pipeline_root.is_dir():
        die(f"PIPELINE_ROOT does not exist or is not a directory: {pipeline_root}")


def load_controller_config() -> Config:
    """Load persisted runtime CONFIG from /runtime/config.toml."""
    config = load_runtime_config()
    env_file = str(config.get("CONFIG_FILE") or DEFAULT_ENV_FILE)
    config["CONFIG_FILE"] = env_file
    config["ENV_FILE"] = env_file

    install_pipeline_defaults(config)
    validate_pipeline_config(config)
    save_config_file(config, env_file)

    return config


def main() -> None:
    print(f"[{timestamp()}] Lab pipeline started", flush=True)

    config = load_controller_config()

    code_root = Path(str(require_config(config, "CODE_ROOT")))
    os.chdir(code_root)

    state = load_or_create_state(config, resume=True)
    state_values(state).setdefault("pipeline_root", str(require_config(config, "PIPELINE_ROOT")))
    save_state_file(state)

    steps = discover_steps(config)
    unit_order, unit_to_step = pipeline_unit_order(config, steps)
    validate_resume_state(
        config=config,
        state=state,
        steps=steps,
        unit_order=unit_order,
        unit_to_step=unit_to_step,
    )
    save_state_file(state)

    log("Discovered pipeline steps:")
    for step in steps:
        log(f"  - {step.name}")

    try:
        for step_script in steps:
            run_hooks(
                kind="PRE",
                step_script=step_script,
                config=config,
                state=state,
            )

            run_step_with_retry(step_script, config, state)

            run_hooks(
                kind="POST",
                step_script=step_script,
                config=config,
                state=state,
            )

        mark_pipeline_ready(state)
        cleanup_marker = Path(str(require_config(config, "GENERATED_DIR"))) / "shutdown-cleanup.json"
        cleanup_marker.unlink(missing_ok=True)
        save_state_file(state)

    except BaseException:
        mark_pipeline_failed(state, exit_code=1)
        save_state_file(state)
        raise


if __name__ == "__main__":
    exit_code = 0

    try:
        main()

    except SystemExit as exc:
        exit_code = int(exc.code) if isinstance(exc.code, int) else 1

    except Exception as exc:
        exit_code = 1
        err(str(exc))
        traceback.print_exc()

    finally:
        duration = int(time.time()) - SCRIPT_START_EPOCH
        end_ts = timestamp()

        if exit_code == 0:
            print(
                f"[{end_ts}] Lab pipeline completed (duration: {duration}s)",
                flush=True,
            )
        else:
            print(
                f"[{end_ts}] Lab pipeline failed with exit code {exit_code} "
                f"(duration: {duration}s)",
                file=sys.stderr,
                flush=True,
            )

    sys.exit(exit_code)
