from __future__ import annotations

import py_compile
import re
import subprocess
import tomllib
from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[2]
TARGETS = ("5Gcore", "opentelemetry")
IGNORED_PARTS = {"__pycache__", "res", ".git", ".venv", "venv", "node_modules", "build", "bin", "obj"}
SERVICE_PARTS = {"containers"}


def relative(path: Path) -> str:
    return str(path.relative_to(ROOT))


def orchestration_python_files() -> list[Path]:
    files: list[Path] = []
    files.extend([ROOT / "start.py", ROOT / "remove_all.py", ROOT / "scripts" / "static_checks.py"])
    files.extend((ROOT / "src" / "lab").rglob("*.py"))
    for target in TARGETS:
        root = ROOT / "src" / target
        for subdir in ("hooks", "conf-files"):
            path = root / subdir
            if path.exists():
                files.extend(path.rglob("*.py"))
    return sorted(
        {
            path
            for path in files
            if path.is_file()
            and not any(part in IGNORED_PARTS or part in SERVICE_PARTS for part in path.relative_to(ROOT).parts)
        }
    )


@pytest.mark.static
def test_orchestration_python_files_compile() -> None:
    failures: list[str] = []
    for path in orchestration_python_files():
        try:
            py_compile.compile(str(path), doraise=True)
        except py_compile.PyCompileError as exc:
            failures.append(f"{relative(path)}: {exc.msg}")

    assert not failures


@pytest.mark.static
def test_shell_entrypoints_parse_with_bash() -> None:
    scripts = [ROOT / "configure_tunnel.sh"]
    scripts.extend((ROOT / "src" / "common").glob("*/docker-entrypoint.sh"))
    scripts.extend((ROOT / "src" / "common").glob("*/entrypoint.sh"))
    failures = []
    for script in sorted(path for path in scripts if path.exists()):
        completed = subprocess.run(["bash", "-n", str(script)], text=True, capture_output=True, check=False)
        if completed.returncode != 0:
            failures.append(f"{relative(script)}: {completed.stderr.strip()}")

    assert not failures


@pytest.mark.static
def test_root_configuration_is_valid_and_defaults_to_5gcore() -> None:
    data = tomllib.loads((ROOT / "configuration.conf").read_text(encoding="utf-8"))

    assert data["lab"]["CLUSTER_TARGET"] == "5Gcore"
    assert "5Gcore" in data["targets"]
    assert "opentelemetry" in data["targets"]


@pytest.mark.static
def test_target_required_conf_files_exist() -> None:
    required = ["compose.yaml.tmpl", "kind-cluster.yaml.tmpl", "skaffold.yaml.tmpl", "images.toml"]
    missing = [
        f"src/{target}/conf-files/{name}"
        for target in TARGETS
        for name in required
        if not (ROOT / "src" / target / "conf-files" / name).is_file()
    ]

    assert not missing


@pytest.mark.static
def test_skaffold_local_charts_skip_dependency_build() -> None:
    errors: list[str] = []
    release_start = re.compile(r"^\s{6}- name:\s*(.+?)\s*$")
    for target in TARGETS:
        template = ROOT / "src" / target / "conf-files" / "skaffold.yaml.tmpl"
        lines = template.read_text(encoding="utf-8").splitlines()
        current_name = ""
        current_start = 0
        current_lines: list[str] = []

        def check_current() -> None:
            if not current_lines or not any(re.search(r"^\s+chartPath:\s*", item) for item in current_lines):
                return
            block = "\n".join(current_lines)
            if not re.search(r"^\s+skipBuildDependencies:\s*true\s*$", block, re.MULTILINE):
                errors.append(f"{relative(template)}:{current_start}: {current_name} uses chartPath without skipBuildDependencies: true")

        for line_no, line in enumerate(lines, 1):
            match = release_start.match(line)
            if match:
                check_current()
                current_name = match.group(1).strip()
                current_start = line_no
                current_lines = [line]
                continue
            if current_lines:
                current_lines.append(line)
        check_current()

    assert not errors


@pytest.mark.static
def test_opentelemetry_cluster_attacker_runs_privileged() -> None:
    template = ROOT / "src" / "opentelemetry" / "conf-files" / "skaffold.yaml.tmpl"
    text = template.read_text(encoding="utf-8")

    assert '"components.test-image.securityContext.privileged": true' in text


@pytest.mark.static
def test_opentelemetry_caldera_smoke_uses_controller_reachable_url() -> None:
    hook = ROOT / "src" / "opentelemetry" / "hooks" / "pipeline" / "HOOK_POST_04.py"
    text = hook.read_text(encoding="utf-8")

    assert 'config_str(CONFIG, "CALDERA_URL", "http://caldera:8888"' in text
    assert 'checks["caldera_http"] = wait_http(caldera_url' in text


@pytest.mark.static
def test_caldera_adversaries_reference_existing_abilities() -> None:
    errors: list[str] = []
    for target in TARGETS:
        ability_ids: set[str] = set()
        duplicate_ids: set[str] = set()
        ability_root = ROOT / "src" / target / "caldera" / "abilities"
        adversary_root = ROOT / "src" / target / "caldera" / "adversaries"

        for path in sorted(ability_root.rglob("*.yml")):
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
            entries = data if isinstance(data, list) else [data]
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                ability_id = str(entry.get("id") or "")
                if not ability_id:
                    errors.append(f"{relative(path)}: missing ability id")
                    continue
                if ability_id in ability_ids:
                    duplicate_ids.add(ability_id)
                ability_ids.add(ability_id)

        for ability_id in sorted(duplicate_ids):
            errors.append(f"src/{target}/caldera: duplicate ability id {ability_id}")

        for path in sorted(adversary_root.glob("*.yml")):
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                errors.append(f"{relative(path)}: adversary YAML is not a mapping")
                continue
            ordering = data.get("atomic_ordering")
            if not isinstance(ordering, list):
                errors.append(f"{relative(path)}: missing atomic_ordering list")
                continue
            for ability_id in ordering:
                if str(ability_id) not in ability_ids:
                    errors.append(f"{relative(path)}: ability id {ability_id} not found")

    assert not errors
