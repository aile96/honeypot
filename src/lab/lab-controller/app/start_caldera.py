#!/usr/bin/env python3
"""Run the generic Caldera kill-chain controller inside the lab container.

The lab itself owns the controller role in the current architecture. This script
loads target defaults, connects to Caldera when enabled,
discovers adversaries, waits for required agents, starts operations, records their
state, and calls target-specific controller hooks before and after operations."""

from __future__ import annotations

import json
import os
import re
import runpy
import signal
import time
import traceback
import urllib.request
import subprocess
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Adversary:
    name: str
    group: str
    key: str
    path: Path
    source_id: str
    attacker_selector: str
    planner: str = "atomic"
    autonomous: int = 1
    auto_close: int = 1


def log(*parts: object) -> None:
    ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
    print(f"[{ts}]", *parts, flush=True)


def env_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return "" if value is None else str(value)


CONFIG_FILE = Path(os.getenv("CONFIG_FILE", "/runtime/config.toml"))


def variables_file_candidates() -> list[str]:
    return [str(CONFIG_FILE)]


def read_variables_values(path: str | Path) -> dict[str, str] | None:
    variables_path = Path(path)
    if not variables_path.is_file():
        return None
    if variables_path.suffix != ".py":
        with variables_path.open("rb") as handle:
            data = tomllib.load(handle)
        table = data.get("config", data)
        if not isinstance(table, dict):
            return None
        return {
            str(name): env_value(value)
            for name, value in table.items()
            if not isinstance(value, dict)
        }

    loaded = runpy.run_path(str(variables_path))
    variables = loaded.get("variables")
    if not isinstance(variables, list):
        return None

    values: dict[str, str] = {}
    for item in variables:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "")).strip()
        if name:
            values[name] = env_value(item.get("value"))
    return values


def load_variables_file(path: str | Path) -> bool:
    values = read_variables_values(path)
    if values is None:
        return False

    for name, value in values.items():
        os.environ.setdefault(name, value)
    return True



def read_caldera_api_key(code_root: str) -> str:
    """Read Caldera red API key from caldera/local.yml without relying on env defaults."""
    local_yml = Path(code_root) / "caldera" / "local.yml"
    try:
        for raw_line in local_yml.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if line.startswith("api_key_red:"):
                return line.split(":", 1)[1].strip().strip("\"'")
    except OSError as exc:
        log(f"controller: could not read Caldera API key from {local_yml}: {exc!r}")
    return ""


def ensure_results_location() -> None:
    """Make the legacy /results path land in the configured per-lab results dir."""
    lab_name = os.getenv("LAB_NAME", "honeypotlab")
    results_dir = Path(os.getenv("RESULTS_DIR") or f"/res/results/{lab_name}")
    os.environ.setdefault("RESULTS_DIR", str(results_dir))

    try:
        results_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        log(f"controller: could not create results dir {results_dir}: {exc!r}")

    alias = Path("/results")
    try:
        if alias.is_symlink():
            if alias.resolve() != results_dir.resolve():
                alias.unlink()
                alias.symlink_to(results_dir, target_is_directory=True)
        elif alias.exists():
            if alias.is_dir() and not any(alias.iterdir()):
                alias.rmdir()
                alias.symlink_to(results_dir, target_is_directory=True)
        else:
            alias.symlink_to(results_dir, target_is_directory=True)
    except OSError as exc:
        log(f"controller: could not map /results to {results_dir}: {exc!r}")

    os.environ.setdefault("KILLCHAIN_SUMMARY_PATH", str(results_dir / "killchain-summary.json"))


def env_bool(name: str, default: bool) -> bool:
    return bool_value(os.getenv(name), default)


def bool_value(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    raw = str(value).strip()
    if raw == "":
        return default
    return raw.lower() not in {"0", "false", "no", "off"}


def env_int(name: str, default: int, minimum: int = 0) -> int:
    raw = os.getenv(name, str(default))
    try:
        return max(minimum, int(raw))
    except ValueError:
        return default


def env_float(name: str, default: float, minimum: float = 0.0) -> float:
    raw = os.getenv(name, str(default))
    try:
        return max(minimum, float(raw))
    except ValueError:
        return default


def bootstrap_environment() -> None:
    loaded = False
    for candidate in variables_file_candidates():
        if candidate and load_variables_file(candidate):
            os.environ.setdefault("ENV_FILE", candidate)
            os.environ.setdefault("CONFIG_FILE", candidate)
            loaded = True
            break
    if not loaded:
        raise SystemExit(f"controller: runtime config not found or invalid: {CONFIG_FILE}")

    code_root = os.getenv("CODE_ROOT", "/workdir/code")
    os.environ.setdefault("CODE_ROOT", code_root)
    os.environ.setdefault("CALDERA_ROOT", str(Path(code_root) / "caldera"))
    os.environ.setdefault("CALDERA_ADVERSARIES_DIR", str(Path(os.environ["CALDERA_ROOT"]) / "adversaries"))
    os.environ.setdefault("CONTROLLER_HOOKS_DIR", str(Path(code_root) / "hooks" / "controller"))
    ensure_results_location()
    api_key = read_caldera_api_key(code_root)
    if api_key:
        os.environ["CALDERA_API_KEY"] = api_key
    os.environ.setdefault("ATT_OUT", os.getenv("ATTACKER", "attacker"))
    os.environ.setdefault("ATT_NS", os.getenv("TST_NAMESPACE", "tst"))

    if not os.getenv("CALDERA_URL"):
        caldera_server = os.getenv("CALDERA_SERVER", "caldera")
        caldera_port = os.getenv("CALDERA_PORT", "8888")
        os.environ["CALDERA_URL"] = f"http://{caldera_server}:{caldera_port}"


bootstrap_environment()

BASE = os.getenv("CALDERA_URL", "http://localhost:8888").rstrip("/")
API_KEY = os.getenv("CALDERA_API_KEY", "")
GROUP_DEFAULT = os.getenv("GROUP", "cluster")
PLANNER = os.getenv("PLANNER", "atomic")
AUTONOMOUS = 1 if env_bool("AUTONOMOUS", True) else 0
AUTO_CLOSE = 1 if env_bool("AUTO_CLOSE", True) else 0
OP_NAME_PREFIX = os.getenv("OP_NAME_PREFIX", "kc-auto-op")
OP_TIMEOUT = env_int("OP_TIMEOUT", 3600, 1)
POLL_INTERVAL = env_float("POLL_INTERVAL", 3.0, 0.1)
DELAY_BETWEEN = env_float("DELAY_BETWEEN", 2.0, 0.0)
RECENT_WINDOW_MIN = env_int("RECENT_WINDOW_MIN", 5, 0)
REQUIRE_AGENT = env_bool("REQUIRE_AGENT", True)
CALDERA_WAIT_TIMEOUT = env_int("CALDERA_WAIT_TIMEOUT", 300, 1)
AGENT_WAIT_TIMEOUT = env_int("AGENT_WAIT_TIMEOUT", 600, 1)
CONTROLLER_HOOKS_DIR = Path(os.getenv("CONTROLLER_HOOKS_DIR", "/workdir/code/hooks/controller"))
SUMMARY_PATH = Path(os.getenv("KILLCHAIN_SUMMARY_PATH", "/results/killchain-summary.json"))
RUN_FILE = Path(os.getenv("KILLCHAIN_RUN_FILE", "/start/run"))
RUN_FILE_WAIT_LOG_INTERVAL = env_float("RUN_FILE_WAIT_LOG_INTERVAL", 60.0, 1.0)


def handle_signals() -> None:
    def stop(signum: int, _frame: object) -> None:
        log(f"controller: received signal {signum}; exiting.")
        raise SystemExit(128 + signum)

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)


def http_ok(url: str, timeout: int = 2) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return 200 <= response.status < 500
    except Exception:
        return False


def rest(payload: dict[str, Any], method: str = "POST", retries: int = 60, sleep: float = 2.0) -> Any:
    data = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if API_KEY:
        headers["KEY"] = API_KEY
    request = urllib.request.Request(f"{BASE}/api/rest", data=data, headers=headers, method=method)
    last_error: Exception | None = None

    for _ in range(retries):
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                body = response.read()
                if body:
                    return json.loads(body.decode("utf-8"))
        except Exception as exc:
            last_error = exc
        time.sleep(sleep)

    raise RuntimeError(f"Caldera REST error after retries: {last_error!r}")


def wait_caldera() -> bool:
    log("controller: waiting for Caldera at", BASE)
    deadline = time.time() + CALDERA_WAIT_TIMEOUT
    while time.time() < deadline:
        if http_ok(BASE):
            return True
        time.sleep(2)
    return False


def current_variables_file() -> Path | None:
    for candidate in variables_file_candidates():
        if not candidate:
            continue
        path = Path(candidate)
        if path.is_file():
            return path
    return None


def runtime_variables_values() -> dict[str, str]:
    variables_path = current_variables_file()
    if variables_path is None:
        return {}

    try:
        values = read_variables_values(variables_path)
    except Exception as exc:
        log(f"controller: could not reload variables from {variables_path}: {exc!r}; using environment/defaults.")
        return {}
    return values or {}


def runtime_bool_from_names(names: list[str], default: bool) -> tuple[bool, str]:
    values = runtime_variables_values()
    for name in names:
        if name in values:
            return bool_value(values[name], default), name

    for name in names:
        raw = os.getenv(name)
        if raw is not None and raw != "":
            return bool_value(raw, default), name

    return default, names[0] if names else ""


def killchain_enable_variable_names(adversary: Adversary) -> list[str]:
    if not adversary.key:
        return []

    match = re.search(r"(\d+)", adversary.key)
    if not match:
        return []

    raw_number = match.group(1)
    normalized_number = str(int(raw_number)) if raw_number else raw_number
    names = [f"ENABLE_KC{normalized_number}"]
    if raw_number != normalized_number:
        names.append(f"ENABLE_KC{raw_number}")
    return names


def killchain_enabled(adversary: Adversary) -> tuple[bool, str]:
    names = killchain_enable_variable_names(adversary)
    if not names:
        return True, ""
    return runtime_bool_from_names(names, True)


def wait_for_run_file(adversary: Adversary) -> tuple[bool, str]:
    try:
        RUN_FILE.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        log(f"controller: could not create run-file directory {RUN_FILE.parent}: {exc!r}")

    waited = False
    last_log = 0.0
    while not RUN_FILE.is_file():
        enabled, enable_variable = killchain_enabled(adversary)
        if not enabled:
            return False, enable_variable

        waited = True
        now = time.time()
        if now - last_log >= RUN_FILE_WAIT_LOG_INTERVAL:
            if RUN_FILE.exists():
                log(f"controller: {RUN_FILE} exists but is not a regular file; waiting before {adversary.key or adversary.name}.")
            else:
                log(f"controller: waiting for run file {RUN_FILE} before starting {adversary.key or adversary.name}.")
            last_log = now
        time.sleep(POLL_INTERVAL)

    if waited:
        log(f"controller: run file {RUN_FILE} found; continuing with {adversary.key or adversary.name}.")
    return True, ""


def destroy_run_file_if_requested(adversary: Adversary) -> None:
    should_destroy, variable_name = runtime_bool_from_names(["DESTROY_RUN_FILE"], False)
    if not should_destroy:
        return

    try:
        if RUN_FILE.is_file() or RUN_FILE.is_symlink():
            RUN_FILE.unlink()
            log(f"controller: removed run file {RUN_FILE} after {adversary.key or adversary.name} ({variable_name}=true).")
        elif RUN_FILE.exists():
            log(f"controller: {variable_name}=true but {RUN_FILE} is not a regular file; leaving it in place.")
    except OSError as exc:
        log(f"controller: could not remove run file {RUN_FILE}: {exc!r}")


def natural_key(value: str) -> list[Any]:
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", value)]


def read_adversary_field(path: Path, field: str) -> str:
    try:
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if line.startswith(f"{field}:"):
                return line.split(":", 1)[1].strip().strip("\"'")
    except OSError as exc:
        log(f"controller: failed reading adversary file {path}: {exc!r}")
    return ""


def read_adversary_name(path: Path) -> str:
    return read_adversary_field(path, "name")


def read_adversary_id(path: Path) -> str:
    return read_adversary_field(path, "id")


def adversary_key(name: str, path: Path, source_id: str = "") -> str:
    for candidate in (path.stem, path.name, name, source_id):
        match = re.search(r"\bKC\s*([0-9]+)\b", candidate, flags=re.IGNORECASE)
        if match:
            return f"KC{match.group(1)}"
        match = re.search(r"\bkc([0-9]+)\b", candidate, flags=re.IGNORECASE)
        if match:
            return f"KC{match.group(1)}"
    return ""


def killchain_id_suffix(source_id: str) -> str:
    """Return the last two numeric digits of a Caldera kill-chain id."""
    digits = re.sub(r"\D", "", source_id or "")
    return digits[-2:] if len(digits) >= 2 else ""


def normalize_attacker_group(value: Any) -> str:
    """Convert id_attackers values to Caldera agent groups."""
    normalized = str(value).strip().lower()
    if normalized in {"underlay", "outside", "out"}:
        return "outside"
    if normalized in {"cluster", "inside", "kubernetes", "k8s"}:
        return "cluster"
    return ""


def normalize_planner(value: Any) -> str:
    planner = str(value or "atomic").strip().lower()
    return planner or "atomic"


def attacker_label_from_group(group: str) -> str:
    return "outside" if group == "outside" else "cluster" if group == "cluster" else group


def id_attackers_key_for_adversary(name: str, path: Path, source_id: str) -> str:
    key = adversary_key(name, path, source_id)
    match = re.search(r"(\d+)", key or "")
    if match:
        return match.group(1).zfill(2)
    return killchain_id_suffix(source_id)


def parse_attacker_profile(mapping_file: Path, key_text: str, value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise SystemExit(
            f"controller: invalid id_attackers entry {key_text!r} in {mapping_file}: "
            "expected an object like {\"group\": \"outside\", \"planner\": \"batch\"}."
        )

    if "group" not in value:
        raise SystemExit(f"controller: invalid id_attackers entry {key_text!r} in {mapping_file}: missing 'group'.")

    group = normalize_attacker_group(value.get("group"))
    if not group:
        raise SystemExit(
            f"controller: invalid id_attackers entry {key_text!r} in {mapping_file}: "
            f"unsupported group {value.get('group')!r}; expected outside or cluster."
        )

    profile = {
        "group": group,
        "planner": normalize_planner(value.get("planner", value.get("scheduler", "atomic"))),
        "autonomous": 1 if bool_value(value.get("autonomous"), True) else 0,
        "auto_close": 1 if bool_value(value.get("auto_close"), True) else 0,
    }
    return profile


def load_id_attacker_profiles() -> dict[str, dict[str, Any]]:
    """Load CALDERA_ROOT/id_attackers as a strict JSON kill-chain profile map.

    New format only:
      {
        "02": {"group": "outside", "planner": "batch", "autonomous": true, "auto_close": false}
      }
    """
    mapping_file = Path(os.getenv("CALDERA_ROOT", "/workdir/code/caldera")) / "id_attackers"
    if not mapping_file.is_file():
        raise SystemExit(f"controller: id_attackers profile mapping not found: {mapping_file}")

    try:
        raw = json.loads(mapping_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"controller: invalid id_attackers JSON {mapping_file}: {exc}") from exc

    if not isinstance(raw, dict):
        raise SystemExit(f"controller: id_attackers must be a JSON object: {mapping_file}")

    parsed: dict[str, dict[str, Any]] = {}
    for key, value in raw.items():
        key_text = str(key).strip()
        if re.fullmatch(r"[0-9]", key_text):
            key_text = key_text.zfill(2)
        elif not re.fullmatch(r"[0-9]{2}", key_text):
            raise SystemExit(f"controller: invalid id_attackers key {key!r}; expected one or two digits.")
        parsed[key_text] = parse_attacker_profile(mapping_file, key_text, value)

    log(f"controller: loaded id_attackers profiles with {len(parsed)} kill-chain entry(ies) from {mapping_file}.")
    return parsed


ID_ATTACKER_PROFILES: dict[str, dict[str, Any]] | None = None


def id_attacker_profiles() -> dict[str, dict[str, Any]]:
    """Return cached id_attackers profiles, loading them on first runtime use."""
    global ID_ATTACKER_PROFILES
    if ID_ATTACKER_PROFILES is None:
        ID_ATTACKER_PROFILES = load_id_attacker_profiles()
    return ID_ATTACKER_PROFILES


def infer_profile(name: str, path: Path, source_id: str) -> tuple[str, str, str, int, int]:
    profiles = id_attacker_profiles()
    profile_key = id_attackers_key_for_adversary(name, path, source_id)
    if not profile_key or profile_key not in profiles:
        raise SystemExit(
            f"controller: no id_attackers profile for adversary {name!r} "
            f"id={source_id or '-'} file={path.name}; expected key {profile_key or '<unknown>'}."
        )

    profile = profiles[profile_key]
    group = str(profile["group"])

    # Environment override is still supported for the group only, but the JSON profile remains mandatory.
    key = adversary_key(name, path, source_id)
    if key and os.getenv(f"{key}_GROUP"):
        group = normalize_attacker_group(os.getenv(f"{key}_GROUP")) or group

    return (
        group,
        attacker_label_from_group(group),
        str(profile.get("planner") or "atomic"),
        int(profile.get("autonomous", 1)),
        int(profile.get("auto_close", 1)),
    )


def discover_adversaries() -> list[Adversary]:
    directory = Path(os.getenv("CALDERA_ADVERSARIES_DIR", ""))
    if not directory.is_dir():
        log(f"controller: adversary directory not found: {directory}")
        return []

    files = sorted(
        [path for path in directory.iterdir() if path.suffix.lower() in {".yml", ".yaml"}],
        key=lambda path: natural_key(path.name),
    )

    adversaries: list[Adversary] = []
    for path in files:
        name = read_adversary_name(path)
        if not name:
            log(f"controller: skipping adversary without name: {path}")
            continue
        source_id = read_adversary_id(path)
        key = adversary_key(name, path, source_id)
        group, selector, planner, autonomous, auto_close = infer_profile(name, path, source_id)
        adversaries.append(
            Adversary(
                name=name,
                group=group,
                key=key,
                path=path,
                source_id=source_id,
                attacker_selector=selector,
                planner=planner,
                autonomous=autonomous,
                auto_close=auto_close,
            )
        )
        log(
            f"controller: discovered adversary {name!r} id={source_id or '-'} "
            f"key={key or '-'} group={group} selector={selector} "
            f"planner={planner} autonomous={autonomous} auto_close={auto_close} file={path.name}"
        )
    return adversaries


def hook_candidates(kind: str, adversary: Adversary | None = None) -> list[Path]:
    base = CONTROLLER_HOOKS_DIR
    candidates = [base / f"HOOK_{kind}.py"]
    if adversary is not None:
        keys = []
        if adversary.key:
            keys.append(adversary.key.upper())
            number = adversary.key.removeprefix("KC")
            if number:
                keys.append(number.zfill(2))
        keys.append(adversary.path.stem)
        for key in keys:
            candidates.append(base / f"HOOK_{kind}_{key}.py")

    unique: list[Path] = []
    seen: set[Path] = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        unique.append(candidate)
    return unique


def run_hooks(
    kind: str,
    adversary: Adversary | None = None,
    *,
    fail_on_error: bool = False,
    extra: dict[str, Any] | None = None,
) -> None:
    if not CONTROLLER_HOOKS_DIR.is_dir():
        return

    for hook in hook_candidates(kind, adversary):
        if not hook.is_file():
            continue

        log(f"controller: running {kind.lower()} hook {hook.name}")
        globals_dict = {
            "HOOK_KIND": kind,
            "HOOK_SCRIPT": str(hook),
            "ADVERSARY": adversary,
            "ADVERSARY_NAME": adversary.name if adversary else "",
            "ADVERSARY_GROUP": adversary.group if adversary else "",
            "KILLCHAIN_KEY": adversary.key if adversary else "",
            "KILLCHAIN_ID": adversary.source_id if adversary else "",
            "KILLCHAIN_ATTACKER_SELECTOR": adversary.attacker_selector if adversary else "",
            "KILLCHAIN_PLANNER": adversary.planner if adversary else "",
            "KILLCHAIN_AUTONOMOUS": adversary.autonomous if adversary else "",
            "KILLCHAIN_AUTO_CLOSE": adversary.auto_close if adversary else "",
            "KILLCHAIN_FILE": str(adversary.path) if adversary else "",
        }
        globals_dict.update(extra or {})

        try:
            runpy.run_path(str(hook), run_name="__main__", init_globals=globals_dict)
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 1
            if code == 0:
                continue
            if fail_on_error:
                raise RuntimeError(f"controller hook {hook.name} exited with {code}") from None
            log(f"controller: hook {hook.name} exited with {code}; continuing.")
        except Exception:
            if fail_on_error:
                raise
            log(f"controller: hook {hook.name} failed; continuing.")
            traceback.print_exc()


def get_adversary_id_by_name(name: str) -> str:
    adversaries = rest({"index": "adversaries"})
    if isinstance(adversaries, list):
        for item in adversaries:
            if isinstance(item, dict) and item.get("name") == name:
                return str(item.get("adversary_id") or "")
    return ""


def wait_agent_in_group(group: str) -> bool:
    if not REQUIRE_AGENT:
        return True

    log("controller: waiting for Caldera agent in group", group)
    deadline = time.time() + AGENT_WAIT_TIMEOUT
    while time.time() < deadline:
        agents = rest({"index": "agents"})
        if isinstance(agents, list) and any(isinstance(agent, dict) and agent.get("group") == group for agent in agents):
            return True
        time.sleep(POLL_INTERVAL)
    return False


def list_operations() -> list[dict[str, Any]]:
    operations = rest({"index": "operations"})
    return operations if isinstance(operations, list) else []


def find_recent_running_op(adversary_id: str, group: str) -> dict[str, Any] | None:
    cutoff = time.time() - RECENT_WINDOW_MIN * 60
    candidates: list[dict[str, Any]] = []

    for operation in list_operations():
        if operation.get("adversary_id") != adversary_id or operation.get("group") != group:
            continue
        started = operation.get("start") or operation.get("start_time") or 0
        try:
            started_float = float(started)
        except (TypeError, ValueError):
            started_float = 0.0
        if started_float >= cutoff and (operation.get("state") in {"running", "started"} or not operation.get("complete")):
            candidates.append(operation)

    if not candidates:
        return None
    return max(candidates, key=lambda item: float(item.get("start") or item.get("start_time") or 0))


def create_operation(adversary_id: str, adversary: Adversary) -> str:
    op_name = f"{OP_NAME_PREFIX}-{int(time.time())}"
    response = rest(
        {
            "index": "operations",
            "name": op_name,
            "adversary_id": adversary_id,
            "planner": adversary.planner,
            "group": adversary.group,
            "autonomous": adversary.autonomous,
            "auto_close": adversary.auto_close,
        },
        method="PUT",
    )

    op_id = ""
    if isinstance(response, dict):
        op_id = str(response.get("id") or response.get("op_id") or "")
    elif isinstance(response, list) and response and isinstance(response[0], dict):
        op_id = str(response[0].get("id") or response[0].get("op_id") or "")

    if not op_id:
        for operation in list_operations():
            if operation.get("name") == op_name and operation.get("id") is not None:
                op_id = str(operation.get("id"))
                break

    if not op_id:
        raise RuntimeError(f"Cannot extract Caldera operation id for {op_name!r}: {response!r}")

    rest({"index": "operation", "op_id": op_id, "state": "running"}, method="POST")
    log(
        f"controller: operation started id={op_id} name={op_name} group={adversary.group} "
        f"planner={adversary.planner} autonomous={adversary.autonomous} auto_close={adversary.auto_close}"
    )
    return op_id


def create_and_start_operation(adversary_id: str, adversary: Adversary) -> str:
    existing = find_recent_running_op(adversary_id, adversary.group)
    if existing:
        op_id = str(existing.get("id"))
        log(f"controller: found recent running operation id={op_id}; reusing.")
        return op_id
    return create_operation(adversary_id, adversary)


def parse_caldera_time(value: Any) -> float:
    if not isinstance(value, str) or not value:
        return 0.0
    try:
        parsed = time.strptime(value.removesuffix("Z"), "%Y-%m-%dT%H:%M:%S")
        return time.mktime(parsed)
    except ValueError:
        return 0.0


def chain_terminal_result(operation: dict[str, Any]) -> tuple[bool, str] | None:
    adversary = operation.get("adversary") if isinstance(operation.get("adversary"), dict) else {}
    expected_order = adversary.get("atomic_ordering") or []
    chain = operation.get("chain") or []

    if not isinstance(chain, list) or not chain:
        return None
    if isinstance(expected_order, list) and expected_order and len(chain) < len(expected_order):
        return None

    pending: list[str] = []
    failed: list[str] = []
    last_activity = 0.0

    for link in chain:
        if not isinstance(link, dict):
            continue
        ability = link.get("ability") if isinstance(link.get("ability"), dict) else {}
        name = str(ability.get("name") or link.get("id") or "unknown")
        status = link.get("status")
        last_activity = max(
            last_activity,
            parse_caldera_time(link.get("finish")),
            parse_caldera_time(link.get("collect")),
            parse_caldera_time(link.get("decide")),
        )

        if status is None:
            pending.append(name)
            continue
        try:
            status_int = int(status)
        except (TypeError, ValueError):
            failed.append(f"{name}=status:{status}")
            continue

        if status_int in {-3, -1}:
            pending.append(name)
        elif status_int != 0:
            failed.append(f"{name}=status:{status_int}")

    if pending:
        return None
    if failed:
        return False, "failed links: " + "; ".join(failed)
    return True, "finished"


def operation_state(op_id: str) -> tuple[str, bool, tuple[bool, str] | None]:
    operation = rest({"index": "operation", "op_id": op_id})
    if isinstance(operation, dict):
        state = str(operation.get("state") or "").lower()
        complete = bool(operation.get("complete") or operation.get("completed") or operation.get("finished"))
        return state, complete, chain_terminal_result(operation)

    for item in list_operations():
        if str(item.get("id")) == str(op_id):
            state = str(item.get("state") or "").lower()
            complete = bool(item.get("complete") or item.get("completed") or item.get("finished"))
            return state, complete, chain_terminal_result(item)
    return "", False, None


def wait_operation_done(op_id: str) -> tuple[bool, str]:
    deadline = time.time() + OP_TIMEOUT
    last = ""
    while time.time() < deadline:
        state, done, terminal = operation_state(op_id)
        if state and state != last:
            log(f"controller: operation {op_id} state={state}")
            last = state
        if done or state in {"finished", "complete", "completed", "success", "stopped"}:
            return True, state or "finished"
        if terminal is not None:
            return terminal
        time.sleep(POLL_INTERVAL)
    return False, last or "timeout"


def restore_hook_candidates(adversary: Adversary) -> list[Path]:
    """Return target restore scripts for the completed kill chain."""
    base = Path(os.getenv("CODE_ROOT", "/workdir/code")) / "hooks" / "restore"
    keys: list[str] = []
    if adversary.key:
        keys.append(adversary.key.upper())
        number = adversary.key.removeprefix("KC")
        if number:
            keys.append(number.zfill(2))
    keys.append(adversary.path.stem)

    candidates = [base / "HOOK_RESTORE.py"]
    for key in keys:
        candidates.append(base / f"HOOK_RESTORE_{key}.py")

    unique: list[Path] = []
    seen: set[Path] = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        unique.append(candidate)
    return unique


def run_soft_restore(adversary: Adversary, op_id: str, ok: bool, state: str) -> None:
    """Run optional target-specific restore scripts after one kill chain."""
    candidates = [path for path in restore_hook_candidates(adversary) if path.is_file()]
    if not candidates:
        log(f"controller: no soft restore script found for {adversary.key or adversary.name}; continuing.")
        return

    for script in candidates:
        log(f"controller: running restore script {script.name}")
        globals_dict = {
            "ADVERSARY": adversary,
            "ADVERSARY_NAME": adversary.name,
            "ADVERSARY_GROUP": adversary.group,
            "KILLCHAIN_KEY": adversary.key,
            "KILLCHAIN_ID": adversary.source_id,
            "KILLCHAIN_ATTACKER_SELECTOR": adversary.attacker_selector,
            "KILLCHAIN_PLANNER": adversary.planner,
            "KILLCHAIN_AUTONOMOUS": adversary.autonomous,
            "KILLCHAIN_AUTO_CLOSE": adversary.auto_close,
            "KILLCHAIN_FILE": str(adversary.path),
            "OP_ID": op_id,
            "OP_OK": ok,
            "OP_STATE": state,
        }
        runpy.run_path(str(script), run_name="__main__", init_globals=globals_dict)


def run_hard_restore() -> None:
    """Reset pipeline state and rerun the lab pipeline."""
    state_file = Path(os.getenv("STATE_FILE", os.path.join(os.getenv("RUNTIME_DIR", "/res/runtime/honeypotlab"), "generated", "lab-state.json")))
    if state_file.exists():
        state_file.unlink()
        log(f"controller: removed pipeline state before hard restore: {state_file}")
    script = Path(os.getenv("FIRST_SCRIPT", "/app/start_lab.py"))
    env = os.environ.copy()
    env.setdefault("PYTHONPATH", "/app")
    completed = subprocess.run([os.sys.executable, str(script)], env=env, text=True, check=False)
    if completed.returncode != 0:
        raise RuntimeError(f"hard restore failed with exit code {completed.returncode}")


def restore_lab_after_killchain(adversary: Adversary, op_id: str, ok: bool, state: str) -> None:
    """Restore lab state after a kill chain when RESTORE_LAB is enabled."""
    if not env_bool("RESTORE_LAB", False):
        return
    mode = os.getenv("RESTORE_LAB_MODE", "soft").strip().lower()
    log(f"controller: restoring lab after {adversary.key or adversary.name} mode={mode}")
    try:
        if mode == "hard":
            run_hard_restore()
        elif mode == "soft":
            run_soft_restore(adversary, op_id, ok, state)
        else:
            log(f"controller: unsupported RESTORE_LAB_MODE={mode!r}; skipping restore.")
    except Exception:
        log("controller: lab restore failed; continuing with next kill chain.")
        traceback.print_exc()


def write_summary(results: list[dict[str, Any]]) -> bool:
    ok_all = bool(results) and all(bool(item.get("ok")) for item in results)
    SUMMARY_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "finished_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "ok": ok_all,
        "results": results,
    }
    tmp = SUMMARY_PATH.with_suffix(SUMMARY_PATH.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    tmp.replace(SUMMARY_PATH)

    log("controller: kill-chain summary")
    for item in results:
        status = "OK" if item.get("ok") else "FAIL"
        log(f"- {item.get('adversary')} [group={item.get('group')} planner={item.get('planner', '-')}] : {status} ({item.get('state')})")
    log(f"controller: wrote summary to {SUMMARY_PATH}")
    return ok_all


def run_sequence(adversaries: list[Adversary]) -> int:
    if not wait_caldera():
        message = f"Caldera unavailable at {BASE} after {CALDERA_WAIT_TIMEOUT}s"
        write_summary(
            [
                {"adversary": item.name, "group": item.group, "key": item.key, "id": item.source_id, "attacker_selector": item.attacker_selector, "ok": False, "state": message}
                for item in adversaries
            ]
        )
        return 0

    results: list[dict[str, Any]] = []
    for index, adversary in enumerate(adversaries, 1):
        log(f"controller: [{index}/{len(adversaries)}] adversary={adversary.name!r} group={adversary.group}")

        enabled, enable_variable = killchain_enabled(adversary)
        if not enabled:
            state = f"{enable_variable}=false"
            log(f"controller: skipping {adversary.key or adversary.name}; {state}.")
            results.append({"adversary": adversary.name, "group": adversary.group, "key": adversary.key, "id": adversary.source_id, "attacker_selector": adversary.attacker_selector, "planner": adversary.planner, "ok": True, "state": state})
            continue

        run_file_ready, enable_variable = wait_for_run_file(adversary)
        if not run_file_ready:
            state = f"{enable_variable}=false"
            log(f"controller: skipping {adversary.key or adversary.name}; {state}.")
            results.append({"adversary": adversary.name, "group": adversary.group, "key": adversary.key, "id": adversary.source_id, "attacker_selector": adversary.attacker_selector, "planner": adversary.planner, "ok": True, "state": state})
            continue

        enabled, enable_variable = killchain_enabled(adversary)
        if not enabled:
            state = f"{enable_variable}=false"
            log(f"controller: skipping {adversary.key or adversary.name}; {state}.")
            results.append({"adversary": adversary.name, "group": adversary.group, "key": adversary.key, "id": adversary.source_id, "attacker_selector": adversary.attacker_selector, "planner": adversary.planner, "ok": True, "state": state})
            continue

        if not wait_agent_in_group(adversary.group):
            state = f"no Caldera agent in group {adversary.group!r} after {AGENT_WAIT_TIMEOUT}s"
            results.append({"adversary": adversary.name, "group": adversary.group, "key": adversary.key, "id": adversary.source_id, "attacker_selector": adversary.attacker_selector, "planner": adversary.planner, "ok": False, "state": state})
            destroy_run_file_if_requested(adversary)
            continue

        adversary_id = ""
        for _ in range(120):
            adversary_id = get_adversary_id_by_name(adversary.name)
            if adversary_id:
                break
            time.sleep(2)

        if not adversary_id:
            state = f"adversary {adversary.name!r} not found in Caldera"
            results.append({"adversary": adversary.name, "group": adversary.group, "key": adversary.key, "id": adversary.source_id, "attacker_selector": adversary.attacker_selector, "planner": adversary.planner, "ok": False, "state": state})
            destroy_run_file_if_requested(adversary)
            continue

        op_id = ""
        try:
            run_hooks("PRE", adversary, fail_on_error=env_bool("CONTROLLER_FAIL_ON_PRE_HOOK", True))
            op_id = create_and_start_operation(adversary_id, adversary)
            ok, state = wait_operation_done(op_id)
        except Exception as exc:
            ok, state = False, repr(exc)
            traceback.print_exc()

        results.append(
            {
                "adversary": adversary.name,
                "group": adversary.group,
                "key": adversary.key,
                "id": adversary.source_id,
                "attacker_selector": adversary.attacker_selector,
                "operation_id": op_id,
                "ok": ok,
                "state": state,
            }
        )
        log(f"controller: adversary {adversary.name!r} completed ok={ok} state={state}")

        run_hooks(
            "POST",
            adversary,
            fail_on_error=env_bool("CONTROLLER_FAIL_ON_POST_HOOK", False),
            extra={"OP_ID": op_id, "OP_OK": ok, "OP_STATE": state},
        )

        restore_lab_after_killchain(adversary, op_id, ok, state)
        destroy_run_file_if_requested(adversary)

        if index < len(adversaries):
            time.sleep(DELAY_BETWEEN)

    write_summary(results)
    return 0


def main() -> int:
    handle_signals()
    run_hooks("START", fail_on_error=env_bool("CONTROLLER_FAIL_ON_START_HOOK", False))

    if not env_bool("CALDERA_SERVER_ENABLE", Path(os.getenv("CALDERA_ROOT", "")).is_dir()):
        log("controller: Caldera disabled or not present; no kill chains will be launched.")
        run_hooks("FINISH", fail_on_error=False, extra={"EXIT_CODE": 0})
        return 0

    adversaries = discover_adversaries()
    if not adversaries:
        log("controller: no adversaries found; exiting.")
        run_hooks("FINISH", fail_on_error=False, extra={"EXIT_CODE": 0})
        return 0

    exit_code = run_sequence(adversaries)
    run_hooks("FINISH", fail_on_error=False, extra={"EXIT_CODE": exit_code})

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
