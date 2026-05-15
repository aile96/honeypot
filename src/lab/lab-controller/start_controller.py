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


def log(*parts: object) -> None:
    ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
    print(f"[{ts}]", *parts, flush=True)


def env_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return "" if value is None else str(value)


def load_variables_file(path: str | Path) -> bool:
    variables_path = Path(path)
    if not variables_path.is_file():
        return False

    loaded = runpy.run_path(str(variables_path))
    variables = loaded.get("variables")
    if not isinstance(variables, list):
        return False

    for item in variables:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "")).strip()
        if name:
            os.environ.setdefault(name, env_value(item.get("value")))
    return True



def read_caldera_api_key(code_root: str) -> str:
    """Read Caldera red API key from caldera/local.yml without relying on variables.py."""
    local_yml = Path(code_root) / "caldera" / "local.yml"
    try:
        for raw_line in local_yml.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if line.startswith("api_key_red:"):
                return line.split(":", 1)[1].strip().strip("\"'")
    except OSError as exc:
        log(f"controller: could not read Caldera API key from {local_yml}: {exc!r}")
    return ""

def env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off"}


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
    candidates = [
        os.getenv("TARGET_CONFIG_FILE", "").strip(),
        os.getenv("ENV_FILE", "").strip(),
        "/workdir/code/conf-files/variables.py",
    ]

    for candidate in candidates:
        if candidate and load_variables_file(candidate):
            os.environ.setdefault("ENV_FILE", candidate)
            break

    code_root = os.getenv("CODE_ROOT", "/workdir/code")
    os.environ.setdefault("CODE_ROOT", code_root)
    os.environ.setdefault("CALDERA_ROOT", str(Path(code_root) / "caldera"))
    os.environ.setdefault("CALDERA_ADVERSARIES_DIR", str(Path(os.environ["CALDERA_ROOT"]) / "adversaries"))
    os.environ.setdefault("CONTROLLER_HOOKS_DIR", str(Path(code_root) / "hooks" / "controller"))
    os.environ.setdefault("KILLCHAIN_SUMMARY_PATH", "/results/killchain-summary.json")
    api_key = read_caldera_api_key(code_root)
    if api_key:
        os.environ["CALDERA_API_KEY"] = api_key
    os.environ.setdefault("ATT_OUT", os.getenv("ATTACKER", "attacker"))
    os.environ.setdefault("ATT_NS", os.getenv("TST_NAMESPACE", "tst"))

    if not os.getenv("CALDERA_URL"):
        os.environ["CALDERA_URL"] = f"http://localhost:{os.getenv('CALDERA_PORT', '8888')}"


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




def attacker_label_from_group(group: str) -> str:
    return "underlay" if group == "outside" else "cluster" if group == "cluster" else group

def load_id_attacker_mapping() -> dict[str, str]:
    """Load CALDERA_ROOT/id_attackers as a JSON suffix-to-attacker map."""
    mapping_file = Path(os.getenv("CALDERA_ROOT", "/workdir/code/caldera")) / "id_attackers"
    if not mapping_file.is_file():
        log(f"controller: id_attackers mapping not found: {mapping_file}; using legacy group inference.")
        return {}

    try:
        raw = json.loads(mapping_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        log(f"controller: invalid id_attackers JSON {mapping_file}: {exc}; using legacy group inference.")
        return {}

    if not isinstance(raw, dict):
        log(f"controller: id_attackers must be a JSON object: {mapping_file}; using legacy group inference.")
        return {}

    parsed: dict[str, str] = {}
    for key, value in raw.items():
        key_text = str(key).strip()
        if re.fullmatch(r"[0-9]", key_text):
            key_text = key_text.zfill(2)
        elif not re.fullmatch(r"[0-9]{2}", key_text):
            log(f"controller: ignoring id_attackers key {key!r}; expected two digits.")
            continue

        group = normalize_attacker_group(value)
        if not group:
            log(f"controller: ignoring id_attackers value {value!r} for suffix {key_text}; expected underlay or cluster.")
            continue
        parsed[key_text] = group

    log(f"controller: loaded id_attackers mapping with {len(parsed)} suffix(es) from {mapping_file}.")
    return parsed


ID_ATTACKER_GROUPS = load_id_attacker_mapping()


def infer_group(name: str, path: Path, source_id: str) -> tuple[str, str]:
    key = adversary_key(name, path, source_id)
    if key and os.getenv(f"{key}_GROUP"):
        group = os.getenv(f"{key}_GROUP", GROUP_DEFAULT)
        return group, attacker_label_from_group(group)

    suffix = killchain_id_suffix(source_id)
    if suffix and suffix in ID_ATTACKER_GROUPS:
        group = ID_ATTACKER_GROUPS[suffix]
        return group, attacker_label_from_group(group)

    if key in {"KC0", "KC1"}:
        return "cluster", "cluster"
    if key:
        return "outside", "underlay"
    return GROUP_DEFAULT, attacker_label_from_group(GROUP_DEFAULT)


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
        group, selector = infer_group(name, path, source_id)
        adversaries.append(
            Adversary(
                name=name,
                group=group,
                key=key,
                path=path,
                source_id=source_id,
                attacker_selector=selector,
            )
        )
        log(
            f"controller: discovered adversary {name!r} id={source_id or '-'} "
            f"key={key or '-'} group={group} selector={selector} file={path.name}"
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


def create_operation(adversary_id: str, group: str) -> str:
    op_name = f"{OP_NAME_PREFIX}-{int(time.time())}"
    response = rest(
        {
            "index": "operations",
            "name": op_name,
            "adversary_id": adversary_id,
            "planner": PLANNER,
            "group": group,
            "autonomous": AUTONOMOUS,
            "auto_close": AUTO_CLOSE,
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
    log(f"controller: operation started id={op_id} name={op_name} group={group} planner={PLANNER}")
    return op_id


def create_and_start_operation(adversary_id: str, group: str) -> str:
    existing = find_recent_running_op(adversary_id, group)
    if existing:
        op_id = str(existing.get("id"))
        log(f"controller: found recent running operation id={op_id}; reusing.")
        return op_id
    return create_operation(adversary_id, group)


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
        log(f"- {item.get('adversary')} [group={item.get('group')}]: {status} ({item.get('state')})")
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

        if not wait_agent_in_group(adversary.group):
            state = f"no Caldera agent in group {adversary.group!r} after {AGENT_WAIT_TIMEOUT}s"
            results.append({"adversary": adversary.name, "group": adversary.group, "key": adversary.key, "id": adversary.source_id, "attacker_selector": adversary.attacker_selector, "ok": False, "state": state})
            continue

        adversary_id = ""
        for _ in range(120):
            adversary_id = get_adversary_id_by_name(adversary.name)
            if adversary_id:
                break
            time.sleep(2)

        if not adversary_id:
            state = f"adversary {adversary.name!r} not found in Caldera"
            results.append({"adversary": adversary.name, "group": adversary.group, "key": adversary.key, "id": adversary.source_id, "attacker_selector": adversary.attacker_selector, "ok": False, "state": state})
            continue

        op_id = ""
        try:
            run_hooks("PRE", adversary, fail_on_error=env_bool("CONTROLLER_FAIL_ON_PRE_HOOK", True))
            op_id = create_and_start_operation(adversary_id, adversary.group)
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
