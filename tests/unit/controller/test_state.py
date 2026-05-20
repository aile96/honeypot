from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.mark.unit
def test_state_lifecycle_records_units_steps_and_ready_status(controller_importer, tmp_path: Path) -> None:
    state_mod = controller_importer.module("lib.state")
    path = tmp_path / "lab-state.json"
    state = state_mod.new_state({"CODE_ROOT": str(tmp_path)}, state_file=path)

    state_mod.record_unit_start(state, "step:01_kind.py", unit_type="step", name="01_kind.py", step_name="01_kind.py")
    state_mod.record_step_start(state, "01_kind.py")
    state_mod.record_step_finish(state, "01_kind.py", exit_code=0)
    state_mod.record_unit_finish(state, "step:01_kind.py", unit_type="step", name="01_kind.py", step_name="01_kind.py", exit_code=0)
    state_mod.mark_pipeline_ready(state)
    state_mod.save_state_file(state)
    loaded = json.loads(path.read_text(encoding="utf-8"))

    assert loaded["status"] == "ready"
    assert "step:01_kind.py" in loaded["completed_units"]
    assert "01_kind.py" in loaded["completed_steps"]
    assert "current_step" not in loaded


@pytest.mark.unit
def test_failed_unit_records_last_failure(controller_importer, tmp_path: Path) -> None:
    state_mod = controller_importer.module("lib.state")
    state = state_mod.new_state({"CODE_ROOT": str(tmp_path)}, state_file=tmp_path / "state.json")

    state_mod.record_unit_start(state, "hook:POST:01_kind.py:HOOK.py", unit_type="hook:POST", name="HOOK.py", step_name="01_kind.py")
    state_mod.record_unit_finish(state, "hook:POST:01_kind.py:HOOK.py", unit_type="hook:POST", name="HOOK.py", step_name="01_kind.py", exit_code=2)

    assert state["status"] == "failed"
    assert state["last_failed_unit"] == "hook:POST:01_kind.py:HOOK.py"
    assert "hook:POST:01_kind.py:HOOK.py" in state["failed_units"]


@pytest.mark.unit
def test_state_values_replaces_invalid_values_mapping(controller_importer) -> None:
    state_mod = controller_importer.module("lib.state")
    state = {"values": []}

    values = state_mod.state_values(state)
    state_mod.set_state_value(state, "k", "v")

    assert values == {"k": "v"}
    assert state_mod.get_state_value(state, "k") == "v"
    assert state_mod.get_state_value({"values": []}, "missing", "default") == "default"


@pytest.mark.unit
def test_load_or_create_state_resumes_existing_file(controller_importer, tmp_path: Path) -> None:
    state_mod = controller_importer.module("lib.state")
    path = tmp_path / "state.json"
    path.write_text('{"status":"ready","completed_units":["x"]}', encoding="utf-8")

    state = state_mod.load_or_create_state({"CODE_ROOT": str(tmp_path)}, resume=True, state_file=path)

    assert state["status"] == "ready"
    assert state["completed_units"] == ["x"]
    assert state["values"] == {}
