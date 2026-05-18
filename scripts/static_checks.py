#!/usr/bin/env python3
"""Run repository-local static checks for the cyber-range lab."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


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


def load_yaml_file(path: Path) -> Any:
    with path.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def yaml_documents(path: Path) -> list[Any]:
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
    for script in ("start.sh", "remove_all.sh"):
        item = run_command(f"bash -n {script}", ["bash", "-n", script])
        if item.status != "PASS":
            result.status = "FAIL"
        result.details.append(f"{script}: {item.status}")
        result.details.extend(f"  {line}" for line in item.details)
    return result


def check_python_compile() -> CheckResult:
    ignored_dirs = {"__pycache__", "node_modules", "res", ".venv", "venv", "env"}
    files = [
        path
        for base in (ROOT / "src", ROOT / "scripts")
        for path in (base.rglob("*.py") if base.exists() else [])
        if not any(part in ignored_dirs for part in path.relative_to(ROOT).parts)
    ]
    result = CheckResult("python compile", "PASS", [f"files={len(files)}"])
    ok = True
    for path in files:
        item = run_command(f"py_compile {rel(path)}", [sys.executable, "-m", "py_compile", str(path)])
        if item.status != "PASS":
            ok = False
            result.details.append(f"{rel(path)}: FAIL")
            result.details.extend(f"  {line}" for line in item.details)
    if not ok:
        result.status = "FAIL"
    return result


def check_yaml_parse() -> CheckResult:
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
    result = CheckResult("caldera integrity", "PASS")
    for target in TARGETS:
        ability_entries = iter_abilities(target)
        adversary_entries = iter_adversaries(target)
        ability_ids: dict[str, Path] = {}
        target_errors: list[str] = []

        for path, entry in ability_entries:
            ability_id = str(entry.get("id") or "")
            if not ability_id:
                target_errors.append(f"{rel(path)}: missing id")
                continue
            if ability_id in ability_ids:
                target_errors.append(f"{rel(path)}: duplicate ability id {ability_id} also in {rel(ability_ids[ability_id])}")
            ability_ids[ability_id] = path

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
            for ability_id in ordering:
                if str(ability_id) not in ability_ids:
                    target_errors.append(f"{rel(path)}: ability id {ability_id} not found")

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
    ignored_dirs = {".git", "__pycache__", "node_modules", "res"}
    occurrences: list[str] = []
    must_review = False

    scan_roots = [
        ROOT / "src",
        ROOT / "scripts",
        ROOT / "start.sh",
        ROOT / "remove_all.sh",
        ROOT / "configuration.conf",
        ROOT / "README.md",
        ROOT / "test-report-codex.md",
    ]
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
        result.status = "REVIEW"
    return result


def check_restore_vars() -> CheckResult:
    result = CheckResult("restore variables placement", "PASS")
    variables_files = {
        ROOT / "src" / "opentelemetry" / "conf-files" / "variables.py",
        ROOT / "src" / "5Gcore" / "conf-files" / "variables.py",
    }
    config_text = (ROOT / "configuration.conf").read_text(encoding="utf-8")
    if "RESTORE_LAB" in config_text or "RESTORE_LAB_MODE" in config_text:
        result.status = "FAIL"
        result.details.append("configuration.conf contains RESTORE_LAB or RESTORE_LAB_MODE")
    else:
        result.details.append("configuration.conf: no RESTORE_LAB declarations")

    for path in sorted(variables_files):
        text = path.read_text(encoding="utf-8")
        missing = [name for name in ("RESTORE_LAB", "RESTORE_LAB_MODE") if name not in text]
        if missing:
            result.status = "FAIL"
            result.details.append(f"{rel(path)}: missing {', '.join(missing)}")
        else:
            result.details.append(f"{rel(path)}: contains RESTORE_LAB and RESTORE_LAB_MODE")

    controller_text = (ROOT / "src" / "lab" / "lab-controller" / "start_controller.py").read_text(encoding="utf-8")
    if "RESTORE_LAB" in controller_text or "RESTORE_LAB_MODE" in controller_text:
        result.details.append("src/lab/lab-controller/start_controller.py: runtime usage present")
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    args = parser.parse_args()

    checks = [
        check_bash_syntax(),
        check_python_compile(),
        check_yaml_parse(),
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
