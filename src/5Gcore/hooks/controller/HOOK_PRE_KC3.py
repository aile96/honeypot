#!/usr/bin/env python3
"""Wait before KC3 until the KC2 mongodb-nwdaf child agent leaves Caldera."""

from __future__ import annotations

import json
import os
import time
import urllib.request
from datetime import datetime, timezone
from typing import Any


DEFAULT_CHILD_PAW = "kc2-mongodb-nwdaf"


def log(*parts: object) -> None:
    print("[5gcore-controller-hook]", *parts, flush=True)


def env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off"}


def env_float(name: str, default: float, minimum: float = 0.0) -> float:
    raw = os.getenv(name, str(default))
    try:
        return max(minimum, float(raw))
    except ValueError:
        return default


def caldera_rest(payload: dict[str, Any], method: str = "POST") -> Any:
    base = os.getenv("CALDERA_URL", "http://caldera:8888").rstrip("/")
    body = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"}

    api_key = os.getenv("CALDERA_API_KEY", "")
    if api_key:
        headers["KEY"] = api_key

    request = urllib.request.Request(
        f"{base}/api/rest",
        data=body,
        headers=headers,
        method=method,
    )

    with urllib.request.urlopen(request, timeout=20) as response:
        raw = response.read()

    return json.loads(raw.decode("utf-8")) if raw else None


def agent_paw(agent: dict[str, Any]) -> str:
    return str(agent.get("paw") or agent.get("paw_id") or "")


def agent_last_seen_age_seconds(agent: dict[str, Any]) -> float | None:
    raw = str(agent.get("last_seen") or "").strip()
    if not raw:
        return None
    try:
        seen = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if seen.tzinfo is None:
        seen = seen.replace(tzinfo=timezone.utc)
    return max(0.0, (datetime.now(timezone.utc) - seen.astimezone(timezone.utc)).total_seconds())


def child_agent_present(child_paw: str, stale_after_seconds: float) -> bool:
    agents = caldera_rest({"index": "agents"})

    if not isinstance(agents, list):
        return False

    for agent in agents:
        if not isinstance(agent, dict) or agent_paw(agent) != child_paw:
            continue
        age = agent_last_seen_age_seconds(agent)
        if age is not None and age > stale_after_seconds:
            log(f"Caldera agent {child_paw!r} is stale; last_seen age={age:.0f}s")
            return False
        return True
    return False


def wait_child_out_of_caldera(child_paw: str, timeout: float, interval: float, stale_after_seconds: float) -> bool:
    deadline = time.time() + timeout

    while time.time() < deadline:
        if not child_agent_present(child_paw, stale_after_seconds):
            return True

        time.sleep(interval)

    return not child_agent_present(child_paw, stale_after_seconds)


def main() -> int:
    if not env_bool("WAIT_KC2_CHILD_OUT_OF_CALDERA", True):
        log("WAIT_KC2_CHILD_OUT_OF_CALDERA=false; skipping KC2 child-agent wait before KC3")
        return 0

    child_paw = os.getenv("KC2_CHILD_PAW", DEFAULT_CHILD_PAW).strip() or DEFAULT_CHILD_PAW
    timeout = env_float("KC2_CHILD_OUT_OF_CALDERA_TIMEOUT", 300.0, 1.0)
    stale_after_seconds = env_float("KC2_CHILD_STALE_AFTER_SECONDS", 120.0, 1.0)
    interval = env_float(
        "KC2_CHILD_OUT_OF_CALDERA_POLL_INTERVAL",
        os.getenv("POLL_INTERVAL", "3"),
        0.1,
    )

    log(f"waiting before KC3 until Caldera agent {child_paw!r} is gone")

    if wait_child_out_of_caldera(child_paw, timeout, interval, stale_after_seconds):
        log(f"Caldera agent {child_paw!r} is gone; KC3 can start")
        return 0

    log(f"timeout after {timeout:g}s: Caldera agent {child_paw!r} is still present")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
