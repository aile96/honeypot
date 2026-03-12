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

  SCRIPT_PRE_KC1,  SCRIPT_PRE_KC2,  ... (str)  script/command to run *before* KC<N>
  SCRIPT_POST_KC1, SCRIPT_POST_KC2, ... (str)  script/command to run *after*  KC<N>
  # NOTE: if empty or unset -> not executed. Executed from cwd=/scripts with shell.

  TELEMETRY_EXPORT_ENABLED     (bool) export snapshots periodically (default: true)
  TELEMETRY_EXPORT_INTERVAL_SEC(int)  interval in seconds (default: 60)
  TELEMETRY_OUTPUT_ROOT        (str)  base output dir (default: /results/telemetry)
  TELEMETRY_BASE_URL           (str)  frontend-proxy base URL (default: http://router:8080)
  TELEMETRY_LOGS_LIMIT         (int)  max logs per snapshot (default: 0 = unlimited)
  TELEMETRY_LOGS_BATCH_SIZE    (int)  logs fetched per OpenSearch page (default: 5000, max: 10000)
  TELEMETRY_LOGS_SCROLL_TTL    (str)  OpenSearch scroll keepalive (default: 1m)
  TELEMETRY_TRACE_LIMIT        (int)  max traces per service (default: 5000)
  TELEMETRY_METRICS_QUERY      (str)  PromQL query for range export (default: {__name__!=""})
  TELEMETRY_METRICS_STEP       (str)  Prometheus range step (default: 15s)
  TELEMETRY_OPENSEARCH_USER/PASS (str) optional Basic auth for OpenSearch API
"""

import base64, json, os, signal, sys, threading, time, urllib.parse, urllib.request, subprocess, shlex, stat
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

def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in ("0", "false", "no", "off")

def _env_int(name: str, default: int, min_value: int = 1) -> int:
    raw = os.getenv(name, str(default))
    try:
        parsed = int(raw)
    except Exception:
        return default
    return max(min_value, parsed)

TELEMETRY_EXPORT_ENABLED = _env_bool("TELEMETRY_EXPORT_ENABLED", True)
TELEMETRY_EXPORT_INTERVAL_SEC = _env_int("TELEMETRY_EXPORT_INTERVAL_SEC", 60, 1)
TELEMETRY_OUTPUT_ROOT = os.getenv("TELEMETRY_OUTPUT_ROOT", "/results/telemetry")
TELEMETRY_BASE_URL = os.getenv("TELEMETRY_BASE_URL", "http://router:8080").rstrip("/")
OPENSEARCH_MAX_RESULT_WINDOW = 10000
TELEMETRY_LOGS_LIMIT = _env_int("TELEMETRY_LOGS_LIMIT", 0, 0)
TELEMETRY_LOGS_BATCH_SIZE = _env_int("TELEMETRY_LOGS_BATCH_SIZE", 5000, 1)
TELEMETRY_LOGS_SCROLL_TTL = os.getenv("TELEMETRY_LOGS_SCROLL_TTL", "1m").strip() or "1m"
TELEMETRY_TRACE_LIMIT = _env_int("TELEMETRY_TRACE_LIMIT", 5000, 1)
TELEMETRY_METRICS_QUERY = os.getenv("TELEMETRY_METRICS_QUERY", '{__name__!=""}')
TELEMETRY_METRICS_STEP = os.getenv("TELEMETRY_METRICS_STEP", "15s").strip() or "15s"
TELEMETRY_OPENSEARCH_USER = os.getenv("TELEMETRY_OPENSEARCH_USER", "").strip()
TELEMETRY_OPENSEARCH_PASS = os.getenv("TELEMETRY_OPENSEARCH_PASS", "").strip()
TELEMETRY_EXPORT_START_TS = time.time()
TELEMETRY_EXPORT_START_ISO = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(TELEMETRY_EXPORT_START_TS))
TELEMETRY_EXPORT_START_US = int(TELEMETRY_EXPORT_START_TS * 1_000_000)

def log(*a, **k):
    ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
    print(f"[{ts}]", *a, flush=True, **k)

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

def _telemetry_dirs(root: str) -> Dict[str, str]:
    dirs = {
        "logs": os.path.join(root, "logs"),
        "traces": os.path.join(root, "traces"),
        "metrics": os.path.join(root, "metrics"),
    }
    for d in dirs.values():
        os.makedirs(d, exist_ok=True)
    return dirs

def _atomic_write_json(path: str, payload: Dict[str, Any]) -> None:
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp_path, path)

def _write_json_if_ok(path: str, payload: Dict[str, Any], ok: bool, kind: str, error_payload: Any) -> None:
    if ok:
        _atomic_write_json(path, payload)
        return
    log(f"auto-starter: {kind} export failed, keeping previous file; error={error_payload!r}")

def _http_json(url: str, method: str = "GET", payload: Optional[Dict[str, Any]] = None, headers: Optional[Dict[str, str]] = None) -> Tuple[bool, Any]:
    req_headers = {"Accept": "application/json"}
    data = None
    if payload is not None:
        req_headers["Content-Type"] = "application/json"
        data = json.dumps(payload).encode("utf-8")
    if headers:
        req_headers.update(headers)
    try:
        req = urllib.request.Request(url, data=data, headers=req_headers, method=method)
        with urllib.request.urlopen(req, timeout=20) as r:
            body = r.read()
            if not body:
                return True, {}
            return True, json.loads(body.decode("utf-8"))
    except Exception as e:
        return False, repr(e)

def _opensearch_headers() -> Dict[str, str]:
    if not TELEMETRY_OPENSEARCH_USER:
        return {}
    token = f"{TELEMETRY_OPENSEARCH_USER}:{TELEMETRY_OPENSEARCH_PASS}".encode("utf-8")
    return {"Authorization": "Basic " + base64.b64encode(token).decode("ascii")}

def _export_logs(dirs: Dict[str, str]) -> None:
    now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    out_path = os.path.join(dirs["logs"], "logs.json")
    logs_limit = TELEMETRY_LOGS_LIMIT
    logs_batch_size = min(TELEMETRY_LOGS_BATCH_SIZE, OPENSEARCH_MAX_RESULT_WINDOW)
    query = {
        "size": logs_batch_size,
        "sort": [{"observedTimestamp": {"order": "desc"}}],
        "query": {
            "bool": {
                "filter": [
                    {
                        "range": {
                            "observedTimestamp": {
                                "gte": TELEMETRY_EXPORT_START_ISO,
                                "lte": "now",
                            }
                        }
                    }
                ]
            }
        },
    }

    headers = _opensearch_headers()
    search_url = f"{TELEMETRY_BASE_URL}/opensearch/otel/_search?scroll={urllib.parse.quote(TELEMETRY_LOGS_SCROLL_TTL, safe='')}"
    scroll_url = f"{TELEMETRY_BASE_URL}/opensearch/_search/scroll"

    ok, first_page = _http_json(search_url, method="POST", payload=query, headers=headers)
    data: Any = first_page

    # Fetch all pages with OpenSearch scroll so we can go beyond max_result_window.
    if ok and isinstance(first_page, dict):
        all_hits: List[Any] = []
        pages_fetched = 0
        scroll_id = first_page.get("_scroll_id")
        page: Dict[str, Any] = first_page
        while True:
            pages_fetched += 1
            hits = page.get("hits", {}).get("hits", [])
            if not isinstance(hits, list):
                hits = []

            if hits:
                if logs_limit > 0:
                    remaining = logs_limit - len(all_hits)
                    if remaining <= 0:
                        break
                    if len(hits) > remaining:
                        hits = hits[:remaining]
                all_hits.extend(hits)

            if not scroll_id or not hits or (logs_limit > 0 and len(all_hits) >= logs_limit):
                break

            ok_scroll, next_page = _http_json(
                scroll_url,
                method="POST",
                payload={"scroll": TELEMETRY_LOGS_SCROLL_TTL, "scroll_id": scroll_id},
                headers=headers,
            )
            if not ok_scroll or not isinstance(next_page, dict):
                ok = False
                data = next_page
                break
            page = next_page
            scroll_id = next_page.get("_scroll_id")

        # Best-effort clear of server-side scroll context.
        if scroll_id:
            _http_json(
                scroll_url,
                method="DELETE",
                payload={"scroll_id": scroll_id},
                headers=headers,
            )

        if ok:
            hits_obj = first_page.get("hits", {})
            if not isinstance(hits_obj, dict):
                hits_obj = {}
            result_obj = dict(first_page)
            result_obj["hits"] = dict(hits_obj)
            result_obj["hits"]["hits"] = all_hits
            result_obj["pages_fetched"] = pages_fetched
            result_obj["returned_hits"] = len(all_hits)
            result_obj.pop("_scroll_id", None)
            data = result_obj

    payload = {
        "ok": ok,
        "exported_at": now_iso,
        "cumulative_from": TELEMETRY_EXPORT_START_ISO,
        "source": search_url,
        "requested_limit": TELEMETRY_LOGS_LIMIT,
        "effective_limit": (None if logs_limit == 0 else logs_limit),
        "batch_size": logs_batch_size,
        "scroll_ttl": TELEMETRY_LOGS_SCROLL_TTL,
        "result": data,
    }
    _write_json_if_ok(out_path, payload, ok, "logs", data)

def _export_traces(dirs: Dict[str, str]) -> None:
    now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    out_path = os.path.join(dirs["traces"], "traces.json")
    services_url = f"{TELEMETRY_BASE_URL}/jaeger/ui/api/services"
    ok_services, services_resp = _http_json(services_url)
    traces_by_service: Dict[str, Any] = {}
    all_ok = ok_services

    end_us = int(time.time() * 1_000_000)
    services = services_resp.get("data", []) if ok_services and isinstance(services_resp, dict) else []
    if not isinstance(services, list):
        all_ok = False
        services = []

    for svc in services:
        if not isinstance(svc, str) or not svc:
            continue
        params = urllib.parse.urlencode({
            "service": svc,
            "limit": TELEMETRY_TRACE_LIMIT,
            "start": TELEMETRY_EXPORT_START_US,
            "end": end_us,
        })
        traces_url = f"{TELEMETRY_BASE_URL}/jaeger/ui/api/traces?{params}"
        ok, traces_resp = _http_json(traces_url)
        if not ok:
            all_ok = False
        traces_by_service[svc] = {
            "ok": ok,
            "source": traces_url,
            "result": traces_resp,
        }

    payload = {
        "ok": ok_services,
        "exported_at": now_iso,
        "cumulative_from": TELEMETRY_EXPORT_START_ISO,
        "services_source": services_url,
        "services_result": services_resp,
        "traces_by_service": traces_by_service,
    }
    _write_json_if_ok(out_path, payload, all_ok, "traces", services_resp)

def _export_metrics(dirs: Dict[str, str]) -> None:
    now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    out_path = os.path.join(dirs["metrics"], "metrics.json")
    query = urllib.parse.urlencode({
        "query": TELEMETRY_METRICS_QUERY,
        "start": str(int(TELEMETRY_EXPORT_START_TS)),
        "end": str(int(time.time())),
        "step": TELEMETRY_METRICS_STEP,
    })
    url = f"{TELEMETRY_BASE_URL}/grafana/api/datasources/proxy/uid/webstore-metrics/api/v1/query_range?{query}"
    ok, data = _http_json(url)
    payload = {
        "ok": ok,
        "exported_at": now_iso,
        "cumulative_from": TELEMETRY_EXPORT_START_ISO,
        "source": url,
        "result": data,
    }
    _write_json_if_ok(out_path, payload, ok, "metrics", data)

def _telemetry_export_once() -> None:
    dirs = _telemetry_dirs(TELEMETRY_OUTPUT_ROOT)
    _export_logs(dirs)
    _export_traces(dirs)
    _export_metrics(dirs)

def _telemetry_export_loop() -> None:
    log(
        "auto-starter: telemetry exporter enabled",
        f"base={TELEMETRY_BASE_URL}",
        f"interval={TELEMETRY_EXPORT_INTERVAL_SEC}s",
        f"output={TELEMETRY_OUTPUT_ROOT}",
    )
    while True:
        started = time.time()
        try:
            _telemetry_export_once()
        except Exception as e:
            log(f"auto-starter: telemetry export iteration failed: {e!r}")
        elapsed = time.time() - started
        time.sleep(max(1.0, TELEMETRY_EXPORT_INTERVAL_SEC - elapsed))

def _start_telemetry_exporter() -> None:
    if not TELEMETRY_EXPORT_ENABLED:
        log("auto-starter: telemetry exporter disabled by TELEMETRY_EXPORT_ENABLED")
        return
    th = threading.Thread(target=_telemetry_export_loop, name="telemetry-exporter", daemon=True)
    th.start()

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

# ---------- Hook runner for pre/post scripts ----------
def _run_hook(var_name: str, idx: int, phase: str) -> None:
    """Executes the command in env var var_name if set and non-empty.
    Runs with shell from cwd=/scripts so relative paths work.
    If the first token is a file in /scripts, ensure it's executable first.
    Never raises; logs return code and output.
    """
    raw = os.getenv(var_name)
    if raw is None or not raw.strip():
        log(f"auto-starter: {phase} hook {var_name} not set -> skip")
        return

    # Normalize quotes the env var might include
    cmd = raw.strip().strip('"').strip("'")

    # If it's a bare filename (no spaces and no slash), make it ./<name>
    if "/" not in cmd and " " not in cmd:
        cmd = f"./{cmd}"

    # Try to make the first token executable when it's a local file
    try:
        # shlex.split is good enough to get the program token
        first_token = shlex.split(cmd, posix=True)[0] if cmd else ""
        # Resolve to a file under /scripts if relative
        if first_token and not os.path.isabs(first_token):
            candidate = os.path.normpath(os.path.join("/scripts", first_token))
        else:
            candidate = first_token

        # Only attempt chmod if it points to a real file (not a shell builtin or program in PATH)
        if candidate and os.path.isfile(candidate):
            st = os.stat(candidate)
            # Add user-execute if missing
            if not (st.st_mode & stat.S_IXUSR):
                os.chmod(candidate, st.st_mode | stat.S_IXUSR)
                log(f"auto-starter: {phase} hook {var_name}: added +x to {candidate}")
    except Exception as e:
        # Don't fail the hook; just log why we couldn't chmod
        log(f"auto-starter: {phase} hook {var_name}: pre-exec chmod skipped: {e!r}")

    log(f"auto-starter: {phase} hook {var_name} -> running: {cmd!r} (cwd=/scripts)")
    try:
        proc = subprocess.run(
            cmd, shell=True, cwd="/scripts", capture_output=True, text=True
        )
        if proc.stdout:
            log(f"auto-starter: {phase} hook stdout:\n{proc.stdout.rstrip()}")
        if proc.stderr:
            log(f"auto-starter: {phase} hook stderr:\n{proc.stderr.rstrip()}")
        log(f"auto-starter: {phase} hook {var_name} exited with code {proc.returncode}")
    except Exception as e:
        log(f"auto-starter: {phase} hook {var_name} failed to start: {e!r}")

def run_sequence(adversaries_with_groups: List[Tuple[str, str]]) -> int:
    wait_caldera()

    results: List[Tuple[str,bool,str,str]] = []  # (adv_name, ok, state, group)
    for i, (adv_name, group) in enumerate(adversaries_with_groups, 1):
        log(f"\n=== [{i}/{len(adversaries_with_groups)}] adversary: {adv_name} (group={group}) ===")

        # waits for an agent in the specific group (if required)
        wait_agent_in_group(group)

        # Resolve adversary id
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

        # ---------- PRE hook for this KC ----------
        _run_hook(f"SCRIPT_PRE_KC{i}", i, "pre")

        # Start / reuse operation
        try:
            op_id = create_and_start_operation_once(adv_id, group)
        except SystemExit as e:
            results.append((adv_name, False, str(e), group))
            if STOP_ON_FAIL: break
            continue

        ok, st = wait_operation_done(op_id, OP_TIMEOUT)
        results.append((adv_name, ok, st, group))
        log(f"auto-starter: adversary '{adv_name}' done -> ok={ok} state={st} (group={group})")

        # ---------- POST hook for this KC ----------
        _run_hook(f"SCRIPT_POST_KC{i}", i, "post")

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
    _start_telemetry_exporter()

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
