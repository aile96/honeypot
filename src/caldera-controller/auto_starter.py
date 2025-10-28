#!/usr/bin/env python3
"""
auto-starter: start one or more kill chains on Caldera in sequence.

Supported ENVs:
  CALDERA_URL   (str)   e.g., http://caldera:8888
  CALDERA_KEY   (str)   API key (optional)

  GROUP         (str)   default agent group (default: cluster)

  ADV_LIST      (str)   Adversary CSV, with optional group mapping:
                       - "KC1,KC2,KC3"
                       - "KC1@grpA, KC2:grpB, KC3"  (use @ or :)
  ADV_NAME      (str)   single fallback if ADV_LIST is empty (uses GROUP)

  ENABLEKC1, ENABLEKC2, ...  (optional)  per-item enable flags.
                              If not set -> enabled. Set to 0/false/no/off to disable.
  PLANNER       (str)   planner (default: batch) e.g., atomic
  AUTONOMOUS    (0/1)   default 1
  AUTO_CLOSE    (0/1)   default 1
  OP_NAME_PREFIX(str)   prefix for operation names (default: kc-auto-op)

  OP_TIMEOUT        (int sec) timeout per single op (default: 3600)
  POLL_INTERVAL     (float s)  status polling (default: 3)
  DELAY_BETWEEN     (float s)  wait between chains (default: 2)
  STOP_ON_FAIL      (bool)     "true"/"false" default true
  RECENT_WINDOW_MIN (int)      minutes for dedup (default: 5)
  REQUIRE_AGENT     (bool)     waits for an agent in the group (default true)
"""

import json, os, signal, sys, time, urllib.request
from typing import Any, Dict, List, Optional, Tuple

BASE = os.getenv("CALDERA_URL", "http://localhost:8888").rstrip("/")
API_KEY = os.getenv("CALDERA_KEY")
GROUP_DEFAULT = os.getenv("GROUP", "cluster")

ADV_LIST_RAW = os.getenv("ADV_LIST", "")
FALLBACK_ADV = os.getenv("ADV_NAME", "KC0 – Test")

PLANNER = os.getenv("PLANNER", "batch")
AUTONOMOUS = int(os.getenv("AUTONOMOUS", "1") or "1")
AUTO_CLOSE = int(os.getenv("AUTO_CLOSE", "1") or "1")
OP_NAME_PREFIX = os.getenv("OP_NAME_PREFIX", "kc-auto-op")

OP_TIMEOUT = int(os.getenv("OP_TIMEOUT", "3600") or "3600")
POLL_INTERVAL = float(os.getenv("POLL_INTERVAL", "3") or "3")
DELAY_BETWEEN = float(os.getenv("DELAY_BETWEEN", "2") or "2")
STOP_ON_FAIL = os.getenv("STOP_ON_FAIL", "true").lower() == "true"
RECENT_WINDOW_MIN = int(os.getenv("RECENT_WINDOW_MIN", "5") or "5")
REQUIRE_AGENT = os.getenv("REQUIRE_AGENT", "true").lower() == "true"

def log(*a, **k): print(*a, flush=True, **k)

def http_ok(url: str, timeout: int = 2) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return 200 <= r.status < 500
    except Exception:
        return False

def rest(payload: Dict[str, Any], method: str = "POST", retries: int = 60, sleep: float = 2.0) -> Any:
    data = json.dumps(payload).encode()
    headers = {"Content-Type": "application/json"}
    if API_KEY: headers["KEY"] = API_KEY
    req = urllib.request.Request(f"{BASE}/api/rest", data=data, headers=headers, method=method)
    last_err = None
    for _ in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                body = r.read()
                if not body: time.sleep(sleep); continue
                return json.loads(body.decode())
        except Exception as e:
            last_err = e
            time.sleep(sleep)
    raise SystemExit(f"auto-starter: REST error after retries: {last_err!r}")

def wait_caldera() -> None:
    log("auto-starter: waiting Caldera on", BASE)
    while not http_ok(BASE): time.sleep(2)

def get_adversary_id_by_name(name: str) -> Optional[str]:
    advs = rest({"index":"adversaries"})
    if isinstance(advs, list):
        for a in advs:
            if a.get("name") == name:
                return a.get("adversary_id")
    return None

def wait_agent_in_group(group: str) -> None:
    if not REQUIRE_AGENT: return
    log("auto-starter: waiting agent in group:", group)
    while True:
        agents = rest({"index":"agents"})
        if isinstance(agents, list) and any(ag.get("group")==group for ag in agents):
            log("auto-starter: agent found in group", group); return
        time.sleep(POLL_INTERVAL)

def list_operations() -> List[Dict[str, Any]]:
    ops = rest({"index":"operations"})
    return ops if isinstance(ops, list) else []

def find_recent_running_op(adv_id: str, group: str, recent_min: int) -> Optional[Dict[str, Any]]:
    cutoff = time.time() - recent_min*60
    cand = []
    for o in list_operations():
        if o.get("adversary_id")==adv_id and o.get("group")==group:
            t = o.get("start") or o.get("start_time") or 0
            try: t = float(t)
            except Exception: t = 0
            if t >= cutoff and ((o.get("state") in ("running","started")) or not o.get("complete")):
                cand.append(o)
    return max(cand, key=lambda x: float(x.get("start") or x.get("start_time") or 0)) if cand else None

def create_operation(adv_id: str, group: str) -> str:
    op_name = f"{OP_NAME_PREFIX}-{int(time.time())}"
    create_payload = {
        "index":"operations",
        "name": op_name,
        "adversary_id": adv_id,
        "planner": PLANNER,
        "group": group,
        "autonomous": AUTONOMOUS,
        "auto_close": AUTO_CLOSE,
    }
    op_resp = rest(create_payload, method="PUT")

    # extract id (can be int or UUID string) -> always treat as string
    op_id: Optional[str] = None
    if isinstance(op_resp, dict):
        op_id = op_resp.get("id") or op_resp.get("op_id")
    elif isinstance(op_resp, list) and op_resp and isinstance(op_resp[0], dict):
        op_id = op_resp[0].get("id") or op_resp[0].get("op_id")

    if op_id is None:
        # fallback by name
        for o in list_operations():
            if o.get("name")==op_name and o.get("id") is not None:
                op_id = str(o.get("id")); break

    if not op_id:
        raise SystemExit(f"auto-starter: cannot extract op id for '{op_name}', resp={op_resp!r}")

    rest({"index":"operation","op_id":op_id,"state":"running"}, method="POST")
    log(f"auto-starter: operation started id={op_id} name={op_name} group={group}")
    return str(op_id)

def create_and_start_operation_once(adv_id: str, group: str) -> str:
    existing = find_recent_running_op(adv_id, group, RECENT_WINDOW_MIN)
    if existing:
        log(f"auto-starter: found recent running op id={existing.get('id')} (group={group}) -> skip creating")
        return str(existing.get("id"))
    return create_operation(adv_id, group)

def operation_state(op_id: str) -> Tuple[str, bool]:
    op = rest({"index":"operation","op_id":op_id})
    if isinstance(op, dict):
        st = (op.get("state") or "").lower()
        complete = bool(op.get("complete") or op.get("completed") or op.get("finished"))
        return st, complete
    for o in list_operations():
        if str(o.get("id")) == str(op_id):
            st = (o.get("state") or "").lower()
            complete = bool(o.get("complete") or o.get("completed") or o.get("finished"))
            return st, complete
    return "", False

def wait_operation_done(op_id: str, timeout_s: int) -> Tuple[bool, str]:
    deadline = time.time() + timeout_s
    last = ""
    while time.time() < deadline:
        st, done = operation_state(op_id)
        if st and st != last:
            log(f"auto-starter: op {op_id} state={st}")
            last = st
        if done or st in ("finished","complete","completed","success","stopped"):
            return True, st or "finished"
        time.sleep(POLL_INTERVAL)
    return False, last or "timeout"

# ---------- ADV_LIST parsing with group mapping ----------
def parse_adv_list(raw: str, default_group: str) -> List[Tuple[str, str]]:
    """
    Accepts comma-separated items.
    Each item can be:
      - "Name"            -> uses default_group
      - "Name@Group"      -> specific group
      - "Name:Group"      -> specific group
    Spaces around tokens are ignored.
    """
    items: List[Tuple[str,str]] = []
    for token in [s.strip() for s in raw.split(",") if s.strip()]:
        name, group = token, default_group
        # supports both '@' and ':'
        if "@" in token or ":" in token:
            # pick the separator that appears last, so we handle names containing ':'
            sep_pos = max(token.rfind("@"), token.rfind(":"))
            name = token[:sep_pos].strip()
            group = token[sep_pos+1:].strip() or default_group
        if name:
            items.append((name, group))
    return items

# ---------- Filtering by ENABLEKC<N> env vars ----------
def _is_disabled_by_env(value: Optional[str]) -> bool:
    if value is None:
        return False  # not set => enabled by default
    v = value.strip().lower()
    return v in ("0", "false", "no", "off")

def filter_by_enable(adversaries: List[Tuple[str,str]]) -> List[Tuple[str,str]]:
    """
    Given a list of (name, group) pairs, check environment variables:
      ENABLEKC1, ENABLEKC2, ...
    The index is 1-based and corresponds to the position in the adv list.
    If ENABLEKC<N> is unset -> item is enabled.
    If ENABLEKC<N> in (0,false,no,off) -> disabled (skipped).
    """
    filtered: List[Tuple[str,str]] = []
    for idx, (name, group) in enumerate(adversaries, start=1):
        env_name = f"ENABLEKC{idx}"
        env_val = os.getenv(env_name)
        if _is_disabled_by_env(env_val):
            log(f"auto-starter: {env_name}={env_val!r} -> SKIP adversary '{name}' (group={group})")
            continue
        # enabled
        if env_val is not None:
            log(f"auto-starter: {env_name}={env_val!r} -> RUN adversary '{name}' (group={group})")
        else:
            log(f"auto-starter: {env_name} not set -> RUN adversary '{name}' (group={group})")
        filtered.append((name, group))
    return filtered

def run_sequence(adversaries_with_groups: List[Tuple[str, str]]) -> int:
    wait_caldera()

    results: List[Tuple[str,bool,str,str]] = []  # (adv_name, ok, state, group)
    for i, (adv_name, group) in enumerate(adversaries_with_groups, 1):
        log(f"\n=== [{i}/{len(adversaries_with_groups)}] adversary: {adv_name} (group={group}) ===")

        # waits for an agent in the specific group (if required)
        wait_agent_in_group(group)

        adv_id = None
        for _ in range(120):  # ~6 min
            adv_id = get_adversary_id_by_name(adv_name)
            if adv_id: break
            time.sleep(2)
        if not adv_id:
            msg = f"adversary '{adv_name}' not found"
            log("auto-starter:", msg)
            results.append((adv_name, False, msg, group))
            if STOP_ON_FAIL: break
            continue

        try:
            op_id = create_and_start_operation_once(adv_id, group)
        except SystemExit as e:
            results.append((adv_name, False, str(e), group))
            if STOP_ON_FAIL: break
            continue

        ok, st = wait_operation_done(op_id, OP_TIMEOUT)
        results.append((adv_name, ok, st, group))
        log(f"auto-starter: adversary '{adv_name}' done -> ok={ok} state={st} (group={group})")
        if i < len(adversaries_with_groups): time.sleep(DELAY_BETWEEN)

    log("\n=== SEQUENCE SUMMARY ===")
    for name, ok, st, group in results:
        log(f"- {name} [group={group}]: {'OK' if ok else 'FAIL'} (state={st})")

    return 0 if results and all(ok for _, ok, _, _ in results) else 1

def _handle_signals():
    def _sig(_n,_f):
        log("auto-starter: received signal, exiting."); sys.exit(0)
    for s in (signal.SIGINT, signal.SIGTERM): signal.signal(s, _sig)

def main():
    _handle_signals()

    adv_with_groups = parse_adv_list(ADV_LIST_RAW, GROUP_DEFAULT)
    if not adv_with_groups:
        # single fallback
        adv_with_groups = [(FALLBACK_ADV, GROUP_DEFAULT)]

    # Filter by ENABLEKC<N> variables (index is 1-based)
    adv_with_groups = filter_by_enable(adv_with_groups)

    if not adv_with_groups:
        log("auto-starter: no adversaries enabled -> exiting")
        sys.exit(0)

    exit_code = run_sequence(adv_with_groups)
    # If you want the pod to exit when it finishes, comment the line below and leave sys.exit:
    signal.pause()
    # sys.exit(exit_code)

if __name__ == "__main__":
    main()
