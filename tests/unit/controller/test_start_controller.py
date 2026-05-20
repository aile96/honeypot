from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.mark.unit
def test_read_variables_values_from_toml_and_python_files(controller_importer, tmp_path: Path) -> None:
    controller = controller_importer.module("app.start_caldera")
    toml_path = tmp_path / "config.toml"
    py_path = tmp_path / "variables.py"
    toml_path.write_text('[config]\nLAB_NAME="honeypotlab"\nHOST_SOCKET=false\nPORT=18080\n', encoding="utf-8")
    py_path.write_text('variables=[{"name":"A","value":1},{"name":"B","value":True}]\n', encoding="utf-8")

    assert controller.read_variables_values(toml_path) == {"LAB_NAME": "honeypotlab", "HOST_SOCKET": "false", "PORT": "18080"}
    assert controller.read_variables_values(py_path) == {"A": "1", "B": "true"}


@pytest.mark.unit
def test_killchain_key_and_enable_variable_names(controller_importer, tmp_path: Path) -> None:
    controller = controller_importer.module("app.start_caldera")
    adversary = controller.Adversary("KC 02 - demo", "outside", "KC02", tmp_path / "kc02.yml", "abc-02", "outside")

    assert controller.adversary_key("KC 02 - demo", tmp_path / "anything.yml") == "KC02"
    assert controller.adversary_key("demo", tmp_path / "kc12.yml") == "KC12"
    assert controller.killchain_enable_variable_names(adversary) == ["ENABLE_KC2", "ENABLE_KC02"]


@pytest.mark.unit
def test_id_attacker_profiles_and_profile_inference(controller_importer, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    caldera_root = tmp_path / "caldera"
    caldera_root.mkdir()
    (caldera_root / "id_attackers").write_text(
        json.dumps(
            {
                "01": {"group": "cluster"},
                "2": {"group": "outside", "planner": "batch", "autonomous": False, "auto_close": False},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("CALDERA_ROOT", str(caldera_root))

    controller = controller_importer.module("app.start_caldera")

    assert controller.load_id_attacker_profiles() == {
        "01": {"group": "cluster", "planner": "atomic", "autonomous": 1, "auto_close": 1},
        "02": {"group": "outside", "planner": "batch", "autonomous": 0, "auto_close": 0},
    }
    assert controller.infer_profile("Whatever", tmp_path / "kc02.yml", "attack-02") == ("outside", "outside", "batch", 0, 0)
    assert controller.normalize_attacker_group("k8s") == "cluster"
    assert controller.normalize_attacker_group("underlay") == "outside"


@pytest.mark.unit
def test_id_attacker_profiles_reject_legacy_string_values(controller_importer, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    caldera_root = tmp_path / "caldera"
    caldera_root.mkdir()
    (caldera_root / "id_attackers").write_text(json.dumps({"01": "cluster"}), encoding="utf-8")
    monkeypatch.setenv("CALDERA_ROOT", str(caldera_root))

    controller = controller_importer.module("app.start_caldera")

    with pytest.raises(SystemExit, match="expected an object"):
        controller.load_id_attacker_profiles()


@pytest.mark.unit
def test_chain_terminal_result(controller_importer) -> None:
    controller = controller_importer.module("app.start_caldera")
    operation = {
        "adversary": {"atomic_ordering": ["a", "b"]},
        "chain": [
            {"ability": {"ability_id": "a", "name": "one"}, "status": 0, "finish": "2025-01-01T00:00:00Z"},
            {"ability": {"ability_id": "b", "name": "two"}, "status": 0, "finish": "2025-01-01T00:00:01Z"},
        ],
    }
    failed = {
        "adversary": {"atomic_ordering": ["a"]},
        "chain": [{"ability": {"ability_id": "a", "name": "one"}, "status": 1}],
    }
    pending = {
        "adversary": {"atomic_ordering": ["a", "b"]},
        "chain": [{"ability": {"ability_id": "a", "name": "one"}, "status": 0}],
    }
    duplicate_missing = {
        "adversary": {"atomic_ordering": ["a", "b", "c"]},
        "chain": [
            {"ability": {"ability_id": "a", "name": "one"}, "status": 0},
            {"ability": {"ability_id": "b", "name": "two"}, "status": 0},
            {"ability": {"ability_id": "b", "name": "two"}, "status": 0},
        ],
    }
    complete_missing = {
        "state": "finished",
        "adversary": {"atomic_ordering": ["a", "b"]},
        "chain": [{"ability": {"ability_id": "a", "name": "one"}, "status": 0}],
    }

    assert controller.chain_terminal_result(operation) == (True, "finished")
    assert controller.chain_terminal_result(failed) == (False, "failed links: one=status:1")
    assert controller.chain_terminal_result(pending) is None
    assert controller.chain_terminal_result(duplicate_missing) is None
    assert controller.chain_terminal_result(complete_missing) == (False, "missing expected links: b")


@pytest.mark.unit
def test_entrypoint_cleanup_values_point_to_generated_compose(controller_importer, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LAB_NAME", "honeypotlab")
    monkeypatch.setenv("RUNTIME_DIR", "/res/runtime/honeypotlab")
    entrypoint = controller_importer.script(
        "controller_entrypoint",
        Path("src/lab/lab-controller/entrypoint.py").resolve(),
    )

    values = entrypoint.cleanup_values({"LAB_NAME": "honeypotlab", "RUNTIME_DIR": "/res/runtime/honeypotlab"})

    assert values["COMPOSE_FILE"] == "/res/runtime/honeypotlab/generated/compose.yaml"
    assert values["COMPOSE_PROJECT_NAME"] == "honeypot-honeypotlab"
