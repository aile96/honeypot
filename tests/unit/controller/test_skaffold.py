from __future__ import annotations

from pathlib import Path

import pytest


@pytest.mark.unit
def test_prepare_writable_helm_charts_copies_to_generated_dir(controller_importer, tmp_path: Path) -> None:
    skaffold = controller_importer.script(
        "skaffold_step",
        Path("src/lab/lab-controller/app/pipeline/04_skaffold.py").resolve(),
    )
    source = tmp_path / "code" / "helm-charts"
    (source / "telemetry" / "tmpcharts-old").mkdir(parents=True)
    (source / "telemetry" / "Chart.yaml").write_text("apiVersion: v2\nname: telemetry\n", encoding="utf-8")
    (source / "telemetry" / "tmpcharts-old" / "stale.txt").write_text("stale\n", encoding="utf-8")

    config = {
        "CODE_ROOT": str(tmp_path / "code"),
        "GENERATED_DIR": str(tmp_path / "runtime" / "generated"),
        "HELM_CHARTS_ROOT": str(source),
    }
    env: dict[str, str] = {}

    destination = skaffold.prepare_writable_helm_charts(config, env)

    assert destination == tmp_path / "runtime" / "generated" / "helm-charts"
    assert (destination / "telemetry" / "Chart.yaml").is_file()
    assert not (destination / "telemetry" / "tmpcharts-old").exists()
    assert config["HELM_CHARTS_ROOT"] == str(destination)
    assert env["HELM_CHARTS_ROOT"] == str(destination)


@pytest.mark.unit
def test_skaffold_insecure_registry_args_include_target_and_cache(controller_importer) -> None:
    skaffold = controller_importer.script(
        "skaffold_step_args",
        Path("src/lab/lab-controller/app/pipeline/04_skaffold.py").resolve(),
    )

    assert skaffold.skaffold_insecure_registry_args(
        {
            "REGISTRY_NAME": "registry",
            "REGISTRY_PORT": 5000,
            "REGISTRY_CACHE_ENDPOINT": "registry-lab:5000",
        }
    ) == [
        "--insecure-registry",
        "registry:5000",
        "--insecure-registry",
        "registry-lab:5000",
    ]

    assert skaffold.skaffold_insecure_registry_args(
        {
            "REGISTRY_NAME": "registry",
            "REGISTRY_PORT": 5000,
            "REGISTRY_CACHE_ENDPOINT": "registry:5000",
        }
    ) == ["--insecure-registry", "registry:5000"]
