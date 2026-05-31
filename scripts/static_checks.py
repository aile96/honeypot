#!/usr/bin/env python3
"""Run offline repository consistency checks for the cyber-range lab."""

from __future__ import annotations

import argparse
import fnmatch
import importlib
import json
import py_compile
import re
import subprocess
import sys
import tomllib
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover - exercised only when optional dev deps are absent
    yaml = None


ROOT = Path(__file__).resolve().parents[1]
IGNORED_DIRS = {".git", "__pycache__", ".pytest_cache", ".venv", "venv", "env", "node_modules", "res", "build", "dist"}
REQUIRED_ABILITY_FIELDS = ("id", "name", "description", "tactic", "technique", "platforms")
REQUIRED_ADVERSARY_FIELDS = ("id", "name", "description", "atomic_ordering")
REQUIRED_TARGET_FILES = (
    "conf-files/compose.yaml.tmpl",
    "conf-files/kind-cluster.yaml.tmpl",
    "conf-files/skaffold.yaml.tmpl",
    "conf-files/images.toml",
    "caldera/local.yml",
    "caldera/id_attackers",
)
REQUIRED_TARGET_DIRS = (
    "caldera/abilities",
    "caldera/adversaries",
    "hooks/controller",
    "hooks/pipeline",
    "hooks/restore",
)
FORBIDDEN_TRACKED_PATTERNS = {
    "res/**": "runtime/output directory",
    "**/__pycache__/**": "Python bytecode cache",
    "**/.pytest_cache/**": "pytest cache",
    "**/*.pyc": "Python bytecode file",
    "**/*.pyo": "Python optimized bytecode file",
    "**/*.log": "runtime log",
    "**/*.tmp": "temporary file",
    "**/*.tgz": "generated Helm dependency package",
    "**/lab-state.json": "runtime lab state",
    "**/killchain-summary.json": "runtime result summary",
    "**/kubeconfig": "runtime kubeconfig",
    ".coverage": "coverage output",
    "htmlcov/**": "coverage HTML output",
}


@dataclass
class CheckResult:
    name: str
    status: str
    details: list[str] = field(default_factory=list)

    @property
    def failed(self) -> bool:
        return self.status == "FAIL"


CheckFn = Any


def rel(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def path_parts(path: Path) -> tuple[str, ...]:
    try:
        return path.relative_to(ROOT).parts
    except ValueError:
        return path.parts


def is_ignored(path: Path) -> bool:
    return any(part in IGNORED_DIRS for part in path_parts(path))


def discover_targets() -> tuple[str, ...]:
    src_root = ROOT / "src"
    if not src_root.exists():
        return ()
    targets: list[str] = []
    for path in src_root.iterdir():
        if not path.is_dir() or path.name in {"common", "lab"}:
            continue
        if (path / "conf-files").exists() or (path / "caldera").exists():
            targets.append(path.name)
    return tuple(sorted(targets, key=str.lower))


TARGETS = discover_targets()


def run_command(command: list[str]) -> tuple[int, list[str]]:
    completed = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, check=False)
    details: list[str] = []
    if completed.stdout.strip():
        details.extend(completed.stdout.strip().splitlines())
    if completed.stderr.strip():
        details.extend(completed.stderr.strip().splitlines())
    return completed.returncode, details


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


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def has_helm_template(path: Path) -> bool:
    try:
        text = read_text(path)
    except UnicodeDecodeError:
        return False
    return "{{" in text or "{%" in text


def has_shell_template_placeholder(path: Path) -> bool:
    try:
        text = read_text(path)
    except UnicodeDecodeError:
        return False
    return "${" in text


def is_yaml_path(path: Path) -> bool:
    name = path.name.lower()
    return name.endswith((".yaml", ".yml", ".yaml.tmpl", ".yml.tmpl"))


def git_tracked_files() -> list[str] | None:
    if not (ROOT / ".git").exists():
        return None
    completed = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=False)
    if completed.returncode != 0:
        return None
    return [item.decode("utf-8", errors="replace") for item in completed.stdout.split(b"\0") if item]


def forbidden_tracked_reason(path: str) -> str | None:
    normalized = path.replace("\\", "/")
    for pattern, reason in FORBIDDEN_TRACKED_PATTERNS.items():
        if fnmatch.fnmatchcase(normalized, pattern):
            return reason
    return None


def check_no_generated_artifacts_tracked() -> CheckResult:
    result = CheckResult("tracked generated artifacts", "PASS")
    tracked = git_tracked_files()
    if tracked is None:
        return CheckResult("tracked generated artifacts", "WARN", ["skipped: no readable Git index"])

    offenders = [(path, forbidden_tracked_reason(path)) for path in tracked if (ROOT / path).exists()]
    offenders = [(path, reason) for path, reason in offenders if reason]
    if offenders:
        result.status = "FAIL"
        result.details.extend(f"{path}: {reason}" for path, reason in offenders)
    else:
        result.details.append(f"checked={len(tracked)}")
    return result


def check_target_contract() -> CheckResult:
    result = CheckResult("target contract", "PASS")
    if not TARGETS:
        return CheckResult("target contract", "FAIL", ["no targets discovered under src/"])

    for target in TARGETS:
        target_root = ROOT / "src" / target
        errors: list[str] = []
        for relative in REQUIRED_TARGET_FILES:
            if not (target_root / relative).is_file():
                errors.append(f"missing file {relative}")
        for relative in REQUIRED_TARGET_DIRS:
            if not (target_root / relative).is_dir():
                errors.append(f"missing directory {relative}")
        result.details.append(f"{target}: errors={len(errors)}")
        result.details.extend(f"  {item}" for item in errors)
        if errors:
            result.status = "FAIL"
    return result


def check_configuration_toml() -> CheckResult:
    result = CheckResult("configuration TOML", "PASS")
    path = ROOT / "configuration.conf"
    try:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
    except FileNotFoundError:
        return CheckResult("configuration TOML", "FAIL", ["configuration.conf not found"])
    except tomllib.TOMLDecodeError as exc:
        return CheckResult("configuration TOML", "FAIL", [f"configuration.conf: {exc}"])

    lab = data.get("lab")
    targets = data.get("targets")
    if not isinstance(lab, dict):
        return CheckResult("configuration TOML", "FAIL", ["configuration.conf: missing [lab] table"])
    if not isinstance(targets, dict):
        return CheckResult("configuration TOML", "FAIL", ["configuration.conf: missing [targets] table"])

    errors: list[str] = []
    configured_target = str(lab.get("CLUSTER_TARGET", "")).strip()
    lab_name = str(lab.get("LAB_NAME", "")).strip()
    if configured_target not in targets:
        errors.append(f"[lab].CLUSTER_TARGET={configured_target!r} has no matching [targets.<name>] table")
    if configured_target and configured_target not in TARGETS:
        errors.append(f"[lab].CLUSTER_TARGET={configured_target!r} has no matching src/<target> directory")
    for target in TARGETS:
        if target not in targets:
            errors.append(f"src/{target} has no [targets.{target}] table")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", lab_name):
        errors.append("[lab].LAB_NAME must contain only letters, digits, dot, underscore, or dash")

    non_negative_prefixes = ("STEP_RETRY_ATTEMPTS_", "STEP_RETRY_DELAY_SECONDS_")
    for key, value in lab.items():
        if key.startswith(non_negative_prefixes):
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                errors.append(f"[lab].{key} must be a non-negative integer")
    for key in ("IMAGE_BUILD_PARALLELISM", "IMAGE_PUSH_PARALLELISM"):
        value = lab.get(key)
        if value is not None and (not isinstance(value, int) or isinstance(value, bool) or value < 1):
            errors.append(f"[lab].{key} must be an integer >= 1")

    result.details.append(f"targets={','.join(TARGETS) or 'none'}")
    result.details.append(f"configured_target={configured_target or 'none'}")
    result.details.extend(errors)
    if errors:
        result.status = "FAIL"
    return result


def check_bash_syntax() -> CheckResult:
    result = CheckResult("shell syntax", "PASS")
    scripts = sorted(path for path in ROOT.rglob("*.sh") if not is_ignored(path))
    result.details.append(f"checked={len(scripts)}")
    for path in scripts:
        returncode, details = run_command(["bash", "-n", rel(path)])
        if returncode != 0:
            result.status = "FAIL"
            result.details.append(f"{rel(path)}: FAIL")
            result.details.extend(f"  {line}" for line in details)
    return result


def check_python_compile() -> CheckResult:
    files = sorted(path for path in ROOT.rglob("*.py") if not is_ignored(path))
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
    paths = sorted(path for path in ROOT.rglob("*") if path.is_file() and is_yaml_path(path) and not is_ignored(path))

    parsed = 0
    skipped_templates = 0
    skipped_shell_templates = 0
    failed = 0
    for path in paths:
        if has_helm_template(path):
            skipped_templates += 1
            continue
        if path.name.endswith(".tmpl") and has_shell_template_placeholder(path):
            skipped_shell_templates += 1
            continue
        try:
            yaml_documents(path)
            parsed += 1
        except Exception as exc:
            failed += 1
            result.status = "FAIL"
            result.details.append(f"{rel(path)}: {exc!r}")

    result.details.extend(
        [
            f"parsed={parsed}",
            f"skipped_templated_yaml={skipped_templates}",
            f"skipped_shell_templates={skipped_shell_templates}",
            f"failed={failed}",
        ]
    )
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
        "LAB_NAME": "cyberrange",
        "IMAGE_VERSION": "2.0.2",
        "REGISTRY_CACHE_ENDPOINT": "registry-lab:5000",
        "CP_NETWORK": "lab",
        "COMPOSE_PORT_BIND_ADDR": "127.0.0.1",
        "RUNTIME_DIR": "/res/runtime/cyberrange",
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
        rendered = substitute_vars(read_text(template), sample_env)
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
    lines = read_text(template).splitlines()
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
    if not template.exists():
        return CheckResult("opentelemetry cluster attacker privileges", "WARN", [f"{rel(template)}: skipped because template is missing"])
    if needle not in read_text(template):
        result.status = "FAIL"
        result.details.append(f"{rel(template)}: test-image must run privileged for its Docker-in-Docker Caldera attacker")
    else:
        result.details.append(f"{rel(template)}: test-image privileged securityContext present")
    return result


def iter_abilities(target: str) -> list[tuple[Path, dict[str, Any]]]:
    abilities: list[tuple[Path, dict[str, Any]]] = []
    base = ROOT / "src" / target / "caldera" / "abilities"
    if not base.exists():
        return abilities
    for path in sorted(base.rglob("*.yml")):
        data = load_yaml_file(path)
        entries = data if isinstance(data, list) else [data]
        for entry in entries:
            if isinstance(entry, dict):
                abilities.append((path, entry))
    return abilities


def iter_adversaries(target: str) -> list[tuple[Path, dict[str, Any]]]:
    adversaries: list[tuple[Path, dict[str, Any]]] = []
    base = ROOT / "src" / target / "caldera" / "adversaries"
    if not base.exists():
        return adversaries
    for path in sorted(base.glob("*.yml")):
        data = load_yaml_file(path)
        if isinstance(data, dict):
            adversaries.append((path, data))
    return adversaries


def is_uuid_like(value: str) -> bool:
    try:
        uuid.UUID(value)
    except ValueError:
        return False
    return True


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
        adversary_ids: dict[str, Path] = {}
        target_errors: list[str] = []
        target_warnings: list[str] = []

        for path, entry in ability_entries:
            ability_id = str(entry.get("id") or "")
            if not ability_id:
                target_errors.append(f"{rel(path)}: missing id")
                continue
            if not is_uuid_like(ability_id):
                target_errors.append(f"{rel(path)}: ability id {ability_id} is not a UUID")
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
            adversary_id = str(adversary.get("id") or "")
            for field_name in REQUIRED_ADVERSARY_FIELDS:
                if field_name not in adversary or adversary.get(field_name) in (None, ""):
                    target_errors.append(f"{rel(path)}: missing {field_name}")
            if adversary_id:
                if not is_uuid_like(adversary_id):
                    target_warnings.append(f"{rel(path)}: adversary id {adversary_id} is not UUID-like; accepted because existing CALDERA data uses KC-prefixed ids")
                if adversary_id in adversary_ids:
                    target_errors.append(f"{rel(path)}: duplicate adversary id {adversary_id} also in {rel(adversary_ids[adversary_id])}")
                adversary_ids[adversary_id] = path

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
            f"{target}: abilities={len(ability_entries)} unique_ids={len(ability_ids)} adversaries={len(adversary_entries)} errors={len(target_errors)} warnings={len(target_warnings)}"
        )
        result.details.extend(f"  {item}" for item in target_errors)
        if target_errors:
            result.status = "FAIL"
    return result


def classify_deprecated_occurrence(path: Path, line: str) -> str | None:
    stripped = line.strip()
    if "GENERIC_SVC_PORT" in line:
        return "deprecated reference"
    if "CLUSTER_PROFILE" in line:
        if "LAB_NAME" in line or "compat" in stripped.lower() or "legacy" in stripped.lower():
            return "compatibility fallback"
        return "legacy cluster-profile reference"
    if re.search(r"\brouter\b", line, re.IGNORECASE):
        if "router" in path.parts or re.search(r"container_name:|service:", line):
            return "possible removed router service"
    return None


def check_deprecated_references() -> CheckResult:
    result = CheckResult("deprecated references", "PASS")
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
        scan_roots.extend([ROOT / "src" / target / "conf-files", ROOT / "src" / target / "hooks"])

    paths: list[Path] = []
    for item in scan_roots:
        if item.is_dir():
            paths.extend(sorted(path for path in item.rglob("*") if path.is_file() and not is_ignored(path)))
        elif item.exists():
            paths.append(item)

    for path in paths:
        try:
            lines = read_text(path).splitlines()
        except (OSError, UnicodeDecodeError):
            continue
        for line_no, line in enumerate(lines, 1):
            classification = classify_deprecated_occurrence(path, line)
            if classification is None:
                continue
            occurrences.append(f"{rel(path)}:{line_no}: {classification}: {line.strip()[:180]}")
            if classification in {"deprecated reference", "possible removed router service", "legacy cluster-profile reference"}:
                must_review = True

    result.details = occurrences or ["no occurrences"]
    if must_review:
        result.status = "WARN"
        result.details.insert(0, "review recommended; warning only")
    return result


def check_restore_vars() -> CheckResult:
    result = CheckResult("restore variables placement", "PASS")
    config = ROOT / "configuration.conf"
    controller = ROOT / "src" / "lab" / "lab-controller" / "app" / "start_caldera.py"
    config_text = read_text(config) if config.exists() else ""
    missing = [name for name in ("RESTORE_LAB", "RESTORE_LAB_MODE") if name not in config_text]
    if missing:
        result.status = "FAIL"
        result.details.append(f"configuration.conf: missing {', '.join(missing)}")
    else:
        result.details.append("configuration.conf: contains RESTORE_LAB and RESTORE_LAB_MODE")

    if controller.exists():
        controller_text = read_text(controller)
        if "RESTORE_LAB" in controller_text or "RESTORE_LAB_MODE" in controller_text:
            result.details.append("src/lab/lab-controller/app/start_caldera.py: runtime usage present")
    return result


CHECKS: dict[str, CheckFn] = {
    "generated": check_no_generated_artifacts_tracked,
    "targets": check_target_contract,
    "config": check_configuration_toml,
    "shell": check_bash_syntax,
    "python": check_python_compile,
    "yaml": check_yaml_parse,
    "compose": check_rendered_compose_templates,
    "skaffold": check_skaffold_local_charts_skip_dependency_build,
    "otel-privileged": check_opentelemetry_test_image_privileged,
    "caldera": check_caldera_integrity,
    "deprecated": check_deprecated_references,
    "restore": check_restore_vars,
}


def main() -> int:
    parser = argparse.ArgumentParser(description="Run offline repository consistency checks.")
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    parser.add_argument("--check", choices=sorted(CHECKS), action="append", help="run only the named check; can be repeated")
    args = parser.parse_args()

    selected_names = args.check or list(CHECKS)
    checks = [CHECKS[name]() for name in selected_names]

    if args.json:
        print(json.dumps([check.__dict__ for check in checks], indent=2))
    else:
        for check in checks:
            print(f"[{check.status}] {check.name}")
            for detail in check.details:
                print(f"  {detail}")

    return 1 if any(check.failed for check in checks) else 0


if __name__ == "__main__":
    raise SystemExit(main())
