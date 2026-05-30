#!/usr/bin/env python3
"""Run repository-local static checks for the cyber-range lab."""

from __future__ import annotations

import argparse
import importlib
import json
import py_compile
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover - exercised only when optional dev deps are absent
    yaml = None


ROOT = Path(__file__).resolve().parents[1]
TARGETS = ("opentelemetry", "5Gcore")
REQUIRED_ABILITY_FIELDS = ("id", "name", "description", "tactic", "technique", "platforms")


@dataclass
class CheckResult:
    name: str
    status: str
    details: list[str] = field(default_factory=list)


def rel(path: Path) -> str:
    return str(path.relative_to(ROOT))


def run_command(name: str, command: list[str]) -> CheckResult:
    completed = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, check=False)
    details: list[str] = []
    if completed.stdout.strip():
        details.extend(completed.stdout.strip().splitlines())
    if completed.stderr.strip():
        details.extend(completed.stderr.strip().splitlines())
    return CheckResult(name=name, status="PASS" if completed.returncode == 0 else "FAIL", details=details)


def require_yaml(check_name: str) -> CheckResult | None:
    if yaml is not None:
        return None
    return CheckResult(check_name, "FAIL", ["PyYAML is required. Install dev dependencies with: python3 -m pip install -r requirements-dev.txt"])


def load_yaml_file(path: Path) -> Any:
    if yaml is None:
        raise RuntimeError("PyYAML is required")
    with path.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def yaml_documents(path: Path) -> list[Any]:
    if yaml is None:
        raise RuntimeError("PyYAML is required")
    with path.open(encoding="utf-8") as handle:
        return list(yaml.safe_load_all(handle))


def has_helm_template(path: Path) -> bool:
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return False
    return "{{" in text or "{%" in text


def has_shell_template_placeholder(path: Path) -> bool:
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return False
    return "${" in text


def check_bash_syntax() -> CheckResult:
    result = CheckResult("shell syntax", "PASS")
    scripts = [ROOT / "configure_tunnel.sh"]
    attacker_root = ROOT / "src" / "common" / "attacker"
    if attacker_root.exists():
        scripts.extend(sorted(attacker_root.rglob("*.sh")))

    checked = 0
    for path in scripts:
        script = rel(path) if path.is_absolute() else str(path)
        if not path.exists():
            result.details.append(f"{script}: SKIP (not present)")
            continue
        checked += 1
        item = run_command(f"bash -n {script}", ["bash", "-n", script])
        if item.status != "PASS":
            result.status = "FAIL"
        result.details.append(f"{script}: {item.status}")
        result.details.extend(f"  {line}" for line in item.details)
    result.details.insert(0, f"checked={checked}")
    return result


def check_python_compile() -> CheckResult:
    ignored_dirs = {"__pycache__", "node_modules", "res", ".venv", "venv", "env", "build", "bin", "obj"}
    ignored_service_dirs = {"containers"}
    files = [path for path in (ROOT / "src" / "lab").rglob("*.py")]
    for target in TARGETS:
        for subdir in ("hooks", "conf-files"):
            base = ROOT / "src" / target / subdir
            if base.exists():
                files.extend(base.rglob("*.py"))
    files.extend(path for path in (ROOT / "scripts" / "static_checks.py", ROOT / "start.py", ROOT / "remove_all.py") if path.exists())
    files = sorted(
        {
            path
            for path in files
            if not any(part in ignored_dirs or part in ignored_service_dirs for part in path.relative_to(ROOT).parts)
        }
    )
    result = CheckResult("python compile", "PASS", [f"files={len(files)}"])
    for path in files:
        try:
            py_compile.compile(str(path), doraise=True)
        except py_compile.PyCompileError as exc:
            result.status = "FAIL"
            result.details.append(f"{rel(path)}: FAIL")
            result.details.append(f"  {exc.msg}")
    return result


def check_yaml_parse() -> CheckResult:
    missing_yaml = require_yaml("yaml parse")
    if missing_yaml is not None:
        return missing_yaml
    result = CheckResult("yaml parse", "PASS")
    paths: set[Path] = set()
    ignored_dirs = {".git", "__pycache__", "node_modules", "res", ".venv", "venv", "env"}

    for path in (
        ROOT / "compose.yaml",
        ROOT / "docker-compose.yaml",
        ROOT / "docker-compose.yml",
    ):
        if path.exists():
            paths.add(path)

    for pattern in (
        "src/**/compose.yaml",
        "src/**/docker-compose*.yaml",
        "src/**/docker-compose*.yml",
        "src/*/caldera/abilities/**/*.yml",
        "src/*/caldera/adversaries/**/*.yml",
        "src/*/conf-files/*.yaml",
        "src/*/conf-files/*.yaml.tmpl",
        "src/*/helm-charts/**/*.yaml",
        "src/*/helm-charts/**/*.yml",
    ):
        paths.update(
            path
            for path in ROOT.glob(pattern)
            if not any(part in ignored_dirs for part in path.relative_to(ROOT).parts)
        )

    parsed = 0
    skipped_templates = 0
    skipped_shell_templates = 0
    failed = 0
    for path in sorted(paths):
        if has_helm_template(path):
            skipped_templates += 1
            continue
        if path.suffix == ".tmpl" and has_shell_template_placeholder(path):
            skipped_shell_templates += 1
            continue
        try:
            yaml_documents(path)
            parsed += 1
        except Exception as exc:
            failed += 1
            result.status = "FAIL"
            result.details.append(f"{rel(path)}: {exc!r}")

    result.details.insert(0, f"parsed={parsed}")
    result.details.insert(1, f"skipped_templated_yaml={skipped_templates}")
    result.details.insert(2, f"skipped_shell_templates={skipped_shell_templates}")
    result.details.insert(3, f"failed={failed}")
    return result


def _load_controller_substitute_vars():
    controller_root = ROOT / "src" / "lab" / "lab-controller"
    shared_root = ROOT / "src" / "lab"
    for name in list(sys.modules):
        if name == "lib" or name.startswith("lib.") or name == "shared" or name.startswith("shared."):
            sys.modules.pop(name, None)
    sys.path.insert(0, str(controller_root))
    sys.path.insert(1, str(shared_root))
    try:
        module = importlib.import_module("lib.utils")
        return module.substitute_vars
    finally:
        for item in (str(controller_root), str(shared_root)):
            try:
                sys.path.remove(item)
            except ValueError:
                pass


def _iter_yaml_strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        strings: list[str] = []
        for item in value:
            strings.extend(_iter_yaml_strings(item))
        return strings
    if isinstance(value, dict):
        strings = []
        for item in value.values():
            strings.extend(_iter_yaml_strings(item))
        return strings
    return []


def check_rendered_compose_templates() -> CheckResult:
    missing_yaml = require_yaml("rendered compose templates")
    if missing_yaml is not None:
        return missing_yaml

    result = CheckResult("rendered compose templates", "PASS")
    try:
        substitute_vars = _load_controller_substitute_vars()
    except Exception as exc:
        return CheckResult("rendered compose templates", "FAIL", [repr(exc)])

    sample_env = {
        "LAB_NAME": "honeypotlab",
        "IMAGE_VERSION": "2.0.2",
        "REGISTRY_CACHE_ENDPOINT": "registry-lab:5000",
        "CP_NETWORK": "lab",
        "COMPOSE_PORT_BIND_ADDR": "127.0.0.1",
        "RUNTIME_DIR": "/res/runtime/honeypotlab",
        "COMPOSE_FREE5GC_CERT_DIR": "/tmp/free5gc-certs",
        "COMPOSE_ATTACKER_IPHOST_FILE": "/tmp/iphost",
        "COMPOSE_ATTACKER_APISERVER_DIR": "/tmp/apiserver",
        "COMPOSE_CALDERA_LOCAL_YML": "/tmp/local.yml",
        "COMPOSE_CALDERA_ABILITIES_DIR": "/tmp/abilities",
        "COMPOSE_CALDERA_ADVERSARIES_DIR": "/tmp/adversaries",
    }
    invalid_shell_dollar = re.compile(r"(?<!\$)\$(?!\$|\{?[A-Za-z_])")

    checked = 0
    for target in TARGETS:
        template = ROOT / "src" / target / "conf-files" / "compose.yaml.tmpl"
        if not template.exists():
            continue
        checked += 1
        rendered = substitute_vars(template.read_text(encoding="utf-8"), sample_env)
        try:
            data = yaml.safe_load(rendered)
        except Exception as exc:
            result.status = "FAIL"
            result.details.append(f"{rel(template)}: rendered YAML parse failed: {exc!r}")
            continue

        for text in _iter_yaml_strings(data):
            match = invalid_shell_dollar.search(text)
            if match:
                result.status = "FAIL"
                snippet = text[max(0, match.start() - 50) : match.start() + 80].replace("\n", "\\n")
                result.details.append(f"{rel(template)}: unescaped Compose dollar near: {snippet}")
                break

    result.details.insert(0, f"checked={checked}")
    return result


def skaffold_local_chart_blocks(template: Path) -> list[tuple[str, int, str]]:
    """Return local Helm release blocks from a Skaffold template."""
    lines = template.read_text(encoding="utf-8").splitlines()
    blocks: list[tuple[str, int, str]] = []
    current_name = ""
    current_start = 0
    current_lines: list[str] = []
    release_start = re.compile(r"^\s{6}- name:\s*(.+?)\s*$")

    for line_no, line in enumerate(lines, 1):
        match = release_start.match(line)
        if match:
            if current_lines and any(re.search(r"^\s+chartPath:\s*", item) for item in current_lines):
                blocks.append((current_name, current_start, "\n".join(current_lines)))
            current_name = match.group(1).strip()
            current_start = line_no
            current_lines = [line]
            continue
        if current_lines:
            current_lines.append(line)

    if current_lines and any(re.search(r"^\s+chartPath:\s*", item) for item in current_lines):
        blocks.append((current_name, current_start, "\n".join(current_lines)))
    return blocks


def check_skaffold_local_charts_skip_dependency_build() -> CheckResult:
    result = CheckResult("skaffold local chart dependency policy", "PASS")
    checked = 0
    for target in TARGETS:
        template = ROOT / "src" / target / "conf-files" / "skaffold.yaml.tmpl"
        if not template.exists():
            continue
        for release_name, start_line, block in skaffold_local_chart_blocks(template):
            checked += 1
            if not re.search(r"^\s+skipBuildDependencies:\s*true\s*$", block, re.MULTILINE):
                result.status = "FAIL"
                result.details.append(f"{rel(template)}:{start_line}: {release_name} uses chartPath without skipBuildDependencies: true")

    result.details.insert(0, f"checked={checked}")
    return result


def check_opentelemetry_test_image_privileged() -> CheckResult:
    template = ROOT / "src" / "opentelemetry" / "conf-files" / "skaffold.yaml.tmpl"
    needle = '"components.test-image.securityContext.privileged": true'
    result = CheckResult("opentelemetry cluster attacker privileges", "PASS")
    if needle not in template.read_text(encoding="utf-8"):
        result.status = "FAIL"
        result.details.append(f"{rel(template)}: test-image must run privileged for its Docker-in-Docker Caldera attacker")
    else:
        result.details.append(f"{rel(template)}: test-image privileged securityContext present")
    return result


def iter_abilities(target: str) -> list[tuple[Path, dict[str, Any]]]:
    abilities: list[tuple[Path, dict[str, Any]]] = []
    for path in sorted((ROOT / "src" / target / "caldera" / "abilities").rglob("*.yml")):
        data = load_yaml_file(path)
        entries = data if isinstance(data, list) else [data]
        for entry in entries:
            if isinstance(entry, dict):
                abilities.append((path, entry))
    return abilities


def iter_adversaries(target: str) -> list[tuple[Path, dict[str, Any]]]:
    adversaries: list[tuple[Path, dict[str, Any]]] = []
    for path in sorted((ROOT / "src" / target / "caldera" / "adversaries").glob("*.yml")):
        data = load_yaml_file(path)
        if isinstance(data, dict):
            adversaries.append((path, data))
    return adversaries


def ability_commands(entry: dict[str, Any]) -> list[str]:
    commands: list[str] = []
    platforms = entry.get("platforms")
    if not isinstance(platforms, dict):
        return commands
    for platform in platforms.values():
        if not isinstance(platform, dict):
            continue
        for executor in platform.values():
            if isinstance(executor, dict):
                command = executor.get("command")
                if isinstance(command, str):
                    commands.append(command)
    return commands


def check_caldera_integrity() -> CheckResult:
    missing_yaml = require_yaml("caldera integrity")
    if missing_yaml is not None:
        return missing_yaml
    result = CheckResult("caldera integrity", "PASS")
    for target in TARGETS:
        ability_entries = iter_abilities(target)
        adversary_entries = iter_adversaries(target)
        ability_ids: dict[str, Path] = {}
        ability_entries_by_id: dict[str, dict[str, Any]] = {}
        target_errors: list[str] = []

        for path, entry in ability_entries:
            ability_id = str(entry.get("id") or "")
            if not ability_id:
                target_errors.append(f"{rel(path)}: missing id")
                continue
            if ability_id in ability_ids:
                target_errors.append(f"{rel(path)}: duplicate ability id {ability_id} also in {rel(ability_ids[ability_id])}")
            ability_ids[ability_id] = path
            ability_entries_by_id[ability_id] = entry

            for field_name in REQUIRED_ABILITY_FIELDS:
                if field_name not in entry or entry.get(field_name) in (None, ""):
                    target_errors.append(f"{rel(path)} ability {ability_id}: missing {field_name}")

            commands = ability_commands(entry)
            if not commands:
                target_errors.append(f"{rel(path)} ability {ability_id}: no executor command")
            elif any(not command.strip() for command in commands):
                target_errors.append(f"{rel(path)} ability {ability_id}: empty executor command")

        for path, adversary in adversary_entries:
            ordering = adversary.get("atomic_ordering")
            if not isinstance(ordering, list):
                target_errors.append(f"{rel(path)}: missing atomic_ordering list")
                continue
            normalized_ordering = [str(ability_id) for ability_id in ordering]
            for ability_id in normalized_ordering:
                if ability_id not in ability_ids:
                    target_errors.append(f"{rel(path)}: ability id {ability_id} not found")
                    continue
                commands = ability_commands(ability_entries_by_id[ability_id])
                if any("dns-poisoning.sh" in command for command in commands) and ability_id != normalized_ordering[-1]:
                    target_errors.append(f"{rel(path)}: dns-poisoning.sh ability {ability_id} must be the last step")

        result.details.append(
            f"{target}: abilities={len(ability_entries)} unique_ids={len(ability_ids)} adversaries={len(adversary_entries)} errors={len(target_errors)}"
        )
        result.details.extend(f"  {item}" for item in target_errors)
        if target_errors:
            result.status = "FAIL"
    return result


def classify_forbidden_occurrence(path: Path, line_no: int, line: str) -> str:
    stripped = line.strip()
    if "CLUSTER_PROFILE" in line:
        if "LAB_NAME" in line or "compat" in stripped.lower() or "legacy" in stripped.lower():
            return "compatibility fallback"
        return "review"
    if "GENERIC_SVC_PORT" in line:
        return "deprecated reference"
    if re.search(r"\brouter\b", line, re.IGNORECASE):
        if "router" in path.parts or re.search(r"container_name:|service:", line):
            return "possible removed router/proxy service"
        return "text/reference"
    if re.search(r"\bPROXY\b", line):
        return "proxy variable/reference"
    return "review"


def check_forbidden_references() -> CheckResult:
    result = CheckResult("deprecated references", "PASS")
    patterns = ("CLUSTER_PROFILE", "GENERIC_SVC_PORT", "PROXY", "router")
    ignored_dirs = {".git", "__pycache__", "node_modules", "res", "containers", "tests"}
    occurrences: list[str] = []
    must_review = False

    scan_roots = [
        ROOT / "src" / "lab",
        ROOT / "start.py",
        ROOT / "remove_all.py",
        ROOT / "configuration.conf",
        ROOT / "README.md",
    ]
    for target in TARGETS:
        scan_roots.extend(
            [
                ROOT / "src" / target / "conf-files",
                ROOT / "src" / target / "hooks",
            ]
        )

    paths: list[Path] = []
    for item in scan_roots:
        if item.is_dir():
            paths.extend(sorted(item.rglob("*")))
        elif item.exists():
            paths.append(item)

    for path in paths:
        try:
            is_file = path.is_file()
        except OSError:
            continue
        if not is_file or any(part in ignored_dirs for part in path.parts):
            continue
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            continue
        for line_no, line in enumerate(lines, 1):
            if not any(pattern in line for pattern in patterns) and not re.search(r"\brouter\b", line, re.IGNORECASE):
                continue
            classification = classify_forbidden_occurrence(path, line_no, line)
            occurrences.append(f"{rel(path)}:{line_no}: {classification}: {line.strip()[:180]}")
            if classification in {"deprecated reference", "possible removed router/proxy service", "review"}:
                must_review = True

    result.details = occurrences or ["no occurrences"]
    if must_review:
        result.details.insert(0, "review-only occurrences found; no static failure")
    return result


def check_restore_vars() -> CheckResult:
    result = CheckResult("restore variables placement", "PASS")
    config_text = (ROOT / "configuration.conf").read_text(encoding="utf-8")
    missing = [name for name in ("RESTORE_LAB", "RESTORE_LAB_MODE") if name not in config_text]
    if missing:
        result.status = "FAIL"
        result.details.append(f"configuration.conf: missing {', '.join(missing)}")
    else:
        result.details.append("configuration.conf: contains RESTORE_LAB and RESTORE_LAB_MODE")

    controller_text = (ROOT / "src" / "lab" / "lab-controller" / "app" / "start_caldera.py").read_text(encoding="utf-8")
    if "RESTORE_LAB" in controller_text or "RESTORE_LAB_MODE" in controller_text:
        result.details.append("src/lab/lab-controller/app/start_caldera.py: runtime usage present")
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    args = parser.parse_args()

    checks = [
        check_bash_syntax(),
        check_python_compile(),
        check_yaml_parse(),
        check_rendered_compose_templates(),
        check_skaffold_local_charts_skip_dependency_build(),
        check_opentelemetry_test_image_privileged(),
        check_caldera_integrity(),
        check_forbidden_references(),
        check_restore_vars(),
    ]

    if args.json:
        print(json.dumps([check.__dict__ for check in checks], indent=2))
    else:
        for check in checks:
            print(f"[{check.status}] {check.name}")
            for detail in check.details:
                print(f"  {detail}")

    return 1 if any(check.status == "FAIL" for check in checks) else 0


if __name__ == "__main__":
    raise SystemExit(main())
