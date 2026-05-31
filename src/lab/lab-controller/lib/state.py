#!/usr/bin/env python3
"""Persist runtime STATE for resumable pipeline execution.

STATE records discoveries and progress that are produced while the lab runs, such
as generated file paths, started services, active contexts, and completed units.
The helpers in this module load, save, and update that JSON state atomically."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, MutableMapping, TypeAlias

from .config import config_path
from .logging import die, warn

State: TypeAlias = dict[str, Any]

DEFAULT_STATE_RELATIVE_PATH = "state/lab-state.json"


def utc_timestamp() -> str:
    """Return an ISO-8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def default_state_file(config: Mapping[str, Any]) -> Path:
    """Return the configured STATE_FILE or CODE_ROOT/state/lab-state.json."""
    explicit = config.get("STATE_FILE")
    if explicit:
        return Path(str(explicit))

    code_root = config_path(config, "CODE_ROOT")
    return code_root / DEFAULT_STATE_RELATIVE_PATH


def new_state(config: Mapping[str, Any], *, state_file: str | Path | None = None) -> State:
    """Create a new empty runtime STATE object."""
    path = Path(state_file) if state_file is not None else default_state_file(config)
    return {
        "schema_version": 1,
        "status": "created",
        "created_at": utc_timestamp(),
        "updated_at": utc_timestamp(),
        "state_file": str(path),
        "steps": [],
        "completed_steps": [],
        "failed_steps": [],
        "units": [],
        "completed_units": [],
        "failed_units": [],
        "values": {},
    }


def load_state_file(path: str | Path) -> State:
    """Load a JSON STATE file."""
    state_path = Path(path)

    try:
        raw = state_path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except FileNotFoundError:
        die(f"State file not found: {state_path}")
    except json.JSONDecodeError as exc:
        die(f"Invalid JSON state file {state_path}: {exc}")

    if not isinstance(data, dict):
        die(f"State file must contain a JSON object: {state_path}")

    return data


def load_or_create_state(
    config: Mapping[str, Any],
    *,
    resume: bool = False,
    state_file: str | Path | None = None,
) -> State:
    """Load existing STATE when resume=True, otherwise create a fresh state."""
    path = Path(state_file) if state_file is not None else default_state_file(config)

    if resume and path.is_file():
        state = load_state_file(path)
        state.setdefault("state_file", str(path))
        state.setdefault("steps", [])
        state.setdefault("completed_steps", [])
        state.setdefault("failed_steps", [])
        state.setdefault("units", [])
        state.setdefault("completed_units", [])
        state.setdefault("failed_units", [])
        state.setdefault("values", {})
        return state

    return new_state(config, state_file=path)


def save_state_file(state: Mapping[str, Any], path: str | Path | None = None) -> None:
    """Atomically save STATE to disk as pretty JSON."""
    state_path = Path(path or state.get("state_file") or "lab-state.json")
    state_path.parent.mkdir(parents=True, exist_ok=True)

    payload = dict(state)
    payload["updated_at"] = utc_timestamp()

    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{state_path.name}.",
        suffix=".tmp",
        dir=str(state_path.parent),
        text=True,
    )

    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(tmp_name, state_path)
    finally:
        try:
            Path(tmp_name).unlink(missing_ok=True)
        except Exception:
            pass


def state_values(state: MutableMapping[str, Any]) -> MutableMapping[str, Any]:
    """Return STATE['values'], creating it when missing."""
    values = state.setdefault("values", {})
    if not isinstance(values, dict):
        warn("STATE['values'] was not a dict; replacing it with an empty dict.")
        values = {}
        state["values"] = values
    return values


def set_state_value(state: MutableMapping[str, Any], name: str, value: Any) -> Any:
    """Set a runtime value under STATE['values'] and return the stored value."""
    state_values(state)[name] = value
    state["updated_at"] = utc_timestamp()
    return value


def get_state_value(
    state: Mapping[str, Any],
    name: str,
    default: Any = None,
) -> Any:
    """Read a runtime value from STATE['values']."""
    values = state.get("values", {})
    if not isinstance(values, dict):
        return default
    return values.get(name, default)


def unit_completed(state: Mapping[str, Any], unit_id: str) -> bool:
    """Return True when a pipeline unit has already completed successfully."""
    completed = state.get("completed_units", [])
    return isinstance(completed, list) and unit_id in completed


def record_unit_start(
    state: MutableMapping[str, Any],
    unit_id: str,
    *,
    unit_type: str,
    name: str,
    step_name: str | None = None,
) -> None:
    """Record that a resumable pipeline unit started."""
    state["status"] = "running"
    state["current_unit"] = unit_id
    state["current_unit_type"] = unit_type
    state["current_unit_name"] = name
    if step_name:
        state["current_step"] = step_name
    if unit_type != "step":
        state["current_hook"] = name

    state.setdefault("units", []).append(
        {
            "id": unit_id,
            "type": unit_type,
            "name": name,
            "step_name": step_name or name,
            "status": "running",
            "started_at": utc_timestamp(),
        }
    )
    state["updated_at"] = utc_timestamp()


def record_unit_finish(
    state: MutableMapping[str, Any],
    unit_id: str,
    *,
    unit_type: str,
    name: str,
    step_name: str | None = None,
    exit_code: int,
) -> None:
    """Record that a resumable pipeline unit finished."""
    status = "completed" if exit_code == 0 else "failed"

    units = state.setdefault("units", [])
    if isinstance(units, list):
        for item in reversed(units):
            if isinstance(item, dict) and item.get("id") == unit_id and item.get("status") == "running":
                item["status"] = status
                item["exit_code"] = exit_code
                item["finished_at"] = utc_timestamp()
                break

    if exit_code == 0:
        completed = state.setdefault("completed_units", [])
        if unit_id not in completed:
            completed.append(unit_id)
        failed = state.setdefault("failed_units", [])
        if isinstance(failed, list) and unit_id in failed:
            failed.remove(unit_id)
    else:
        failed = state.setdefault("failed_units", [])
        if unit_id not in failed:
            failed.append(unit_id)
        state["last_failed_unit"] = unit_id
        state["last_failed_unit_type"] = unit_type
        state["last_failed_unit_name"] = name
        if step_name:
            state["last_failed_step"] = step_name

    state["status"] = status if exit_code != 0 else "running"
    state.pop("current_unit", None)
    state.pop("current_unit_type", None)
    state.pop("current_unit_name", None)
    if unit_type != "step":
        state.pop("current_hook", None)
    if unit_type == "step" or not step_name:
        state.pop("current_step", None)
    state.pop("current_unit", None)
    state.pop("current_unit_type", None)
    state.pop("current_unit_name", None)
    state.pop("current_hook", None)
    state["updated_at"] = utc_timestamp()


def record_step_start(state: MutableMapping[str, Any], step_name: str) -> None:
    """Record that a step started."""
    state["status"] = "running"
    state["current_step"] = step_name
    state.setdefault("steps", []).append(
        {
            "name": step_name,
            "status": "running",
            "started_at": utc_timestamp(),
        }
    )
    state["updated_at"] = utc_timestamp()


def record_step_finish(
    state: MutableMapping[str, Any],
    step_name: str,
    *,
    exit_code: int,
) -> None:
    """Record that a step finished."""
    status = "completed" if exit_code == 0 else "failed"

    steps = state.setdefault("steps", [])
    if isinstance(steps, list):
        for item in reversed(steps):
            if isinstance(item, dict) and item.get("name") == step_name and item.get("status") == "running":
                item["status"] = status
                item["exit_code"] = exit_code
                item["finished_at"] = utc_timestamp()
                break

    if exit_code == 0:
        completed = state.setdefault("completed_steps", [])
        if step_name not in completed:
            completed.append(step_name)
    else:
        failed = state.setdefault("failed_steps", [])
        if step_name not in failed:
            failed.append(step_name)

    state["status"] = status if exit_code != 0 else "running"
    state.pop("current_step", None)
    state["updated_at"] = utc_timestamp()


def mark_pipeline_ready(state: MutableMapping[str, Any]) -> None:
    """Mark the whole pipeline as ready."""
    state["status"] = "ready"
    state["ready_at"] = utc_timestamp()
    state.pop("current_step", None)
    state.pop("current_hook", None)
    state.pop("current_unit", None)
    state.pop("current_unit_type", None)
    state.pop("current_unit_name", None)
    state["updated_at"] = utc_timestamp()


def mark_pipeline_failed(state: MutableMapping[str, Any], *, exit_code: int) -> None:
    """Mark the whole pipeline as failed."""
    state["status"] = "failed"
    state["exit_code"] = exit_code
    state["failed_at"] = utc_timestamp()
    state["updated_at"] = utc_timestamp()


def record_resume_check(
    state: MutableMapping[str, Any],
    step_name: str,
    *,
    ok: bool,
    reason: str,
) -> None:
    """Record the outcome of a resume-time validation check."""
    checks = state.setdefault("resume_checks", [])
    if not isinstance(checks, list):
        checks = []
        state["resume_checks"] = checks
    checks.append(
        {
            "step": step_name,
            "ok": bool(ok),
            "reason": str(reason),
            "checked_at": utc_timestamp(),
        }
    )
    state["updated_at"] = utc_timestamp()


def prune_completed_units_from_step(
    state: MutableMapping[str, Any],
    step_name: str,
    *,
    step_order: list[str],
    unit_order: list[str],
    unit_to_step: Mapping[str, str],
) -> list[str]:
    """Invalidate completed steps/units from step_name onward.

    Hooks are treated as units owned by their related pipeline step, so invalidating
    a step also invalidates its PRE/POST hooks and every later step/hook.
    """
    if step_name not in step_order:
        return []

    affected_steps = set(step_order[step_order.index(step_name) :])
    affected_units = [unit_id for unit_id in unit_order if unit_to_step.get(unit_id) in affected_steps]

    for key, affected in (
        ("completed_steps", affected_steps),
        ("failed_steps", affected_steps),
    ):
        values = state.get(key, [])
        if isinstance(values, list):
            state[key] = [value for value in values if value not in affected]

    for key in ("completed_units", "failed_units"):
        values = state.get(key, [])
        if isinstance(values, list):
            state[key] = [value for value in values if value not in affected_units]

    current_step = state.get("current_step")
    if isinstance(current_step, str) and current_step in affected_steps:
        state.pop("current_step", None)

    current_unit = state.get("current_unit")
    if isinstance(current_unit, str) and current_unit in affected_units:
        state.pop("current_unit", None)
        state.pop("current_unit_type", None)
        state.pop("current_unit_name", None)
        state.pop("current_hook", None)

    state["status"] = "created"
    state["updated_at"] = utc_timestamp()
    return affected_units
