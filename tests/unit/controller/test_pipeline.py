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
    assert pipeline.step_key_from_script(script) == "03_BUILD_AND_DEPLOY"
    assert pipeline.resolve_step_retry_policy(script, {"PIPELINE_STEP_RETRIES": 2}) == (2, 0)
    assert pipeline.resolve_step_retry_policy(
        script,
        {
            "PIPELINE_STEP_RETRIES": 2,
            "STEP_RETRY_ATTEMPTS_03_BUILD_AND_DEPLOY": 0,
            "STEP_RETRY_DELAY_SECONDS_03_BUILD_AND_DEPLOY": 5,
        },
    ) == (0, 5)


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
        "PIPELINE_STEP_RETRIES": 0,
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
