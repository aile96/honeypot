#!/usr/bin/env python3
"""Run the CONFIG/STATE-driven lab pipeline inside the controller container.

This runner loads the selected target variables.py file, overlays only approved
environment values, installs derived paths, validates pipeline-owned settings, and
executes each pipeline step with matching PRE/POST hooks. CONFIG stores desired
configuration, while STATE stores runtime discoveries and resumable progress."""

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
    config_bool,
    config_to_env,
    die,
    discover_pipeline_scripts,
    err,
    load_or_create_state,
    load_variables_file,
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
    save_state_file,
    set_config_default,
    state_values,
    timestamp,
    unit_completed,
    warn,
)

SCRIPT_START_EPOCH = int(time.time())

DEFAULT_ENV_FILE = "/workdir/code/conf-files/variables.py"
DEFAULT_CODE_ROOT = "/workdir/code"
DEFAULT_RES_DIR = "/res"
DEFAULT_PIPELINE_ROOT = "/app/pipeline"

_MISSING = object()


# Dockerfile ENV values that are still meaningful to the pipeline runner.
DOCKERFILE_PIPELINE_ENV_KEYS = (
    "CLUSTER_PROFILE",
    "ENV_FILE",
    "RES_DIR",
    "GENERIC_SVC_PORT",
    "EXPOSE_TO_HOST",
    "HOST_SOCKET",
    "PROXY_BIND_ALL",
)


# Environment values passed by start.sh through docker run -e and still useful to the pipeline.
START_SH_PIPELINE_ENV_KEYS = (
    "CODE_ROOT",
    "ENV_FILE",
    "CLUSTER_PROFILE",
    "RES_DIR",
    "GENERIC_SVC_PORT",
    "EXPOSE_TO_HOST",
    "HOST_SOCKET",
    "PROXY_BIND_ALL",
    "BUILD_HELPER_CACHE_DIR",
    "HOST_CODE_ROOT",
    "HOST_RES_DIR",
    "HOST_RUNTIME_DIR",
    "HOST_CONTROLLER_RESULTS_DIR",
    "HOST_CONTROLLER_DOCKER_DATA_DIR",
    "HOST_BUILD_HELPER_CACHE_DIR",
)


PIPELINE_ALLOWED_ENV_KEYS = tuple(
    dict.fromkeys((*DOCKERFILE_PIPELINE_ENV_KEYS, *START_SH_PIPELINE_ENV_KEYS))
)


PIPELINE_REQUIRED_CONFIG_KEYS = (
    "CODE_ROOT",
    "ENV_FILE",
    "RES_DIR",
    "CLUSTER_PROFILE",
    "GENERIC_SVC_PORT",
    "EXPOSE_TO_HOST",
    "BUILD_HELPER_CACHE_DIR",
    "HOST_CODE_ROOT",
    "HOST_RES_DIR",
    "HOST_RUNTIME_DIR",
    "HOST_CONTROLLER_RESULTS_DIR",
    "HOST_CONTROLLER_DOCKER_DATA_DIR",
    "HOST_BUILD_HELPER_CACHE_DIR",
)


PIPELINE_BOOL_CONFIG_KEYS = (
    "EXPOSE_TO_HOST",
)


PIPELINE_OPTIONAL_BOOL_CONFIG_KEYS = (
    "PIPELINE_FAIL_ON_POST_HOOK",
    "PROXY_BIND_ALL",
)


PIPELINE_ABSOLUTE_PATH_KEYS = (
    "CODE_ROOT",
    "ENV_FILE",
    "RES_DIR",
    "BUILD_HELPER_CACHE_DIR",
    "HOST_CODE_ROOT",
    "HOST_RES_DIR",
    "HOST_RUNTIME_DIR",
    "HOST_CONTROLLER_RESULTS_DIR",
    "HOST_CONTROLLER_DOCKER_DATA_DIR",
    "HOST_BUILD_HELPER_CACHE_DIR",
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
    "CACHE_DIR",
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


def run_unit_with_retry(
    *,
    script_path: Path,
    config: Config,
    state: State,
    unit_id: str,
    unit_type: str,
    name: str,
    step_name: str | None,
    init_globals: dict[str, Any],
    fail_on_error: bool,
) -> bool:
    """Run a step or hook with unit-specific retry support and resume skipping."""
    if unit_completed(state, unit_id):
        log(f"Skipping completed {unit_type}: {name}")
        return True

    retries, delay = resolve_step_retry_policy(script_path, config)
    total_runs = retries + 1
    log(f"Retry policy for {unit_type} {name}: retries={retries}, delay={delay}s.")

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
    if fail_on_error:
        die(message)

    err(f"{message} Continuing.")
    return False


def run_hooks(
    *,
    kind: str,
    step_script: str | Path,
    config: Config,
    state: State,
    fail_on_error: bool,
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
                fail_on_error=fail_on_error,
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
        config=config,
        state=state,
        unit_id=step_unit_id(step_path),
        unit_type="step",
        name=step_path.name,
        step_name=step_path.name,
        init_globals=step_globals(config=config, state=state, step_path=step_path),
        fail_on_error=True,
    )


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
    """Overlay only Dockerfile/start.sh env vars allowed for the pipeline."""
    for name in PIPELINE_ALLOWED_ENV_KEYS:
        value = os.environ.get(name)
        if value is not None and value != "":
            config[name] = value


def install_pipeline_defaults(config: Config) -> None:
    """Install defaults computed by the pipeline runner itself."""
    code_root = Path(str(config.get("CODE_ROOT", DEFAULT_CODE_ROOT)))
    set_config_default(config, "CODE_ROOT", str(code_root))

    set_config_default(config, "PIPELINE_ROOT", DEFAULT_PIPELINE_ROOT)
    pipeline_root = Path(str(config["PIPELINE_ROOT"]))

    set_config_default(config, "HOOKS_DIR", str(code_root / "hooks" / "pipeline"))
    set_config_default(config, "CONTROLLER_HOOKS_DIR", str(code_root / "hooks" / "controller"))
    set_config_default(config, "CONTAINERS_ROOT", str(code_root / "containers"))
    set_config_default(config, "CALDERA_ROOT", str(code_root / "caldera"))
    set_config_default(config, "CALDERA_ADVERSARIES_DIR", str(code_root / "caldera" / "adversaries"))
    set_config_default(config, "HELM_CHARTS_ROOT", str(code_root / "helm-charts"))

    res_dir = str(config.get("RES_DIR", DEFAULT_RES_DIR))
    runtime_dir = Path(res_dir) / "runtime"
    generated_dir = runtime_dir / "generated"
    results_dir = Path(res_dir) / "results"

    set_config_default(config, "RES_DIR", res_dir)
    set_config_default(config, "RUNTIME_DIR", str(runtime_dir))
    set_config_default(config, "GENERATED_DIR", str(generated_dir))
    set_config_default(config, "RESULTS_DIR", str(results_dir))
    set_config_default(config, "STATE_FILE", str(generated_dir / "lab-state.json"))
    set_config_default(config, "STATE_DIR", str(generated_dir))
    set_config_default(config, "CACHE_DIR", str(Path(res_dir) / "cache" / "images"))

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

    require_port_config(config, "GENERIC_SVC_PORT")

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
    """Load CONFIG from variables.py and allowed controller env overrides."""
    env_file = os.environ.get("ENV_FILE", DEFAULT_ENV_FILE)

    config = load_variables_file(env_file)
    config["ENV_FILE"] = env_file

    overlay_allowed_environment(config)
    install_pipeline_defaults(config)
    validate_pipeline_config(config)

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
                fail_on_error=True,
            )

            run_step_with_retry(step_script, config, state)

            run_hooks(
                kind="POST",
                step_script=step_script,
                config=config,
                state=state,
                fail_on_error=config_bool(config, "PIPELINE_FAIL_ON_POST_HOOK", False),
            )

        mark_pipeline_ready(state)
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