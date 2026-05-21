from __future__ import annotations

import tomllib
from pathlib import Path

import pytest


@pytest.mark.unit
def test_discover_pipeline_scripts_sorts_and_ignores_helpers(controller_importer, tmp_path: Path) -> None:
    pipeline = controller_importer.module("lib.pipeline")
    root = tmp_path / "pipeline"
    root.mkdir()
    for name in ["02_second.py", "01_first.py", "_private.py", "__init__.py", "notes.txt"]:
        (root / name).write_text("", encoding="utf-8")
    (root / "03_directory.py").mkdir()

    discovered = pipeline.discover_pipeline_scripts(root)

    assert [path.name for path in discovered] == ["01_first.py", "02_second.py"]


@pytest.mark.unit
def test_step_names_and_retry_policy(controller_importer) -> None:
    pipeline = controller_importer.module("lib.pipeline")
    script = Path("03_build-and_deploy.py")

    assert pipeline.step_index_from_script(script) == "03"
    assert pipeline.step_slug_from_script(script) == "build-and_deploy"
    assert pipeline.resolve_step_retry_policy(
        script,
        {
            "STEP_RETRY_ATTEMPTS_03": 0,
            "STEP_RETRY_DELAY_SECONDS_03": 5,
        },
    ) == (0, 5)

    with pytest.raises(SystemExit):
        pipeline.resolve_step_retry_policy(script, {"STEP_RETRY_ATTEMPTS_03": 2})

    with pytest.raises(SystemExit):
        pipeline.resolve_step_retry_policy(Path("compose.py"), {})


@pytest.mark.unit
def test_resolve_hook_candidates_uses_generic_and_slug_specific_names(controller_importer, tmp_path: Path) -> None:
    pipeline = controller_importer.module("lib.pipeline")

    candidates = pipeline.resolve_hook_candidates(tmp_path, "post", Path("01_kind.py"))

    assert candidates == [tmp_path / "HOOK_POST_01.py", tmp_path / "HOOK_POST_01_kind.py"]
    assert pipeline.resolve_hook_candidates(tmp_path, "pre", Path("kind.py")) == []


@pytest.mark.unit
def test_parse_pipeline_steps(controller_importer) -> None:
    pipeline = controller_importer.module("lib.pipeline")

    assert pipeline.parse_pipeline_steps(None, ["a", "b"]) == ["a", "b"]
    assert pipeline.parse_pipeline_steps("a, b,,c", []) == ["a", "b", "c"]
    assert pipeline.parse_pipeline_steps(["a", 2], []) == ["a", "2"]
    with pytest.raises(SystemExit):
        pipeline.parse_pipeline_steps(123, [])


@pytest.mark.unit
def test_run_unit_once_persists_config_mutations(controller_importer, tmp_path: Path) -> None:
    start_lab = controller_importer.script(
        "start_lab_runner",
        Path("src/lab/lab-controller/app/start_lab.py").resolve(),
    )
    state_mod = controller_importer.module("lib.state")
    config_path = tmp_path / "config.toml"
    unit_script = tmp_path / "mutate_config.py"
    unit_script.write_text('CONFIG["KIND_REGISTRY_ENDPOINT"] = "registry:5000"\n', encoding="utf-8")
    config = {
        "CONFIG_FILE": str(config_path),
        "ENV_FILE": str(config_path),
        "CODE_ROOT": str(tmp_path),
    }
    state = state_mod.new_state(config, state_file=tmp_path / "state.json")

    rc = start_lab.run_unit_once(
        script_path=unit_script,
        config=config,
        state=state,
        unit_id="hook:PRE:01_kind.py:HOOK_PRE_01.py",
        unit_type="hook:PRE",
        name="HOOK_PRE_01.py",
        step_name="01_kind.py",
        init_globals={"CONFIG": config, "STATE": state},
    )

    assert rc == 0
    assert tomllib.loads(config_path.read_text(encoding="utf-8"))["config"]["KIND_REGISTRY_ENDPOINT"] == "registry:5000"


@pytest.mark.unit
def test_hook_retry_uses_parent_step_number(controller_importer, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    start_lab = controller_importer.script(
        "start_lab_runner",
        Path("src/lab/lab-controller/app/start_lab.py").resolve(),
    )
    state_mod = controller_importer.module("lib.state")

    config_path = tmp_path / "config.toml"
    step_script = tmp_path / "04_skaffold.py"
    hook_script = tmp_path / "HOOK_POST_04.py"
    attempts_file = tmp_path / "attempts.txt"

    step_script.write_text("", encoding="utf-8")
    hook_script.write_text(
        "from pathlib import Path\n"
        f"p = Path({str(attempts_file)!r})\n"
        "n = int(p.read_text() or '0') if p.exists() else 0\n"
        "p.write_text(str(n + 1))\n"
        "raise SystemExit(1 if n == 0 else 0)\n",
        encoding="utf-8",
    )

    config = {
        "CONFIG_FILE": str(config_path),
        "ENV_FILE": str(config_path),
        "CODE_ROOT": str(tmp_path),
        "STEP_RETRY_ATTEMPTS_04": 1,
        "STEP_RETRY_DELAY_SECONDS_04": 0,
    }
    state = state_mod.new_state(config, state_file=tmp_path / "state.json")

    slept: list[int] = []
    monkeypatch.setattr(start_lab.time, "sleep", lambda seconds: slept.append(seconds))

    ok = start_lab.run_unit_with_retry(
        script_path=hook_script,
        retry_policy_source=step_script,
        config=config,
        state=state,
        unit_id="hook:POST:04_skaffold.py:HOOK_POST_04.py",
        unit_type="hook:POST",
        name=hook_script.name,
        step_name=step_script.name,
        init_globals={"CONFIG": config, "STATE": state},
    )

    assert ok is True
    assert attempts_file.read_text(encoding="utf-8") == "2"
    assert slept == [0]
