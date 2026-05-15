#!/usr/bin/env python3
"""Start periodic OpenTelemetry telemetry export in the controller process.

This controller hook launches a lightweight background exporter when telemetry is
enabled. It periodically snapshots logs, traces, and metrics while kill chains run
so post-run analysis can correlate attacks with observable telemetry."""

from __future__ import annotations

import json
import os
import threading
import time
import urllib.parse
import urllib.request
from typing import Any


def log(*parts: object) -> None:
    print("[otel-telemetry-export]", *parts, flush=True)


def env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off"}


def env_int(name: str, default: int, minimum: int = 0) -> int:
    raw = os.getenv(name, str(default))
    try:
        return max(minimum, int(raw))
    except ValueError:
        return default


ENABLED = env_bool("TELEMETRY_EXPORT_ENABLED", True)
INTERVAL = env_int("TELEMETRY_EXPORT_INTERVAL_SEC", 60, 1)
OUTPUT_ROOT = os.getenv("TELEMETRY_OUTPUT_ROOT", "/results/telemetry")
BASE_URL = os.getenv("TELEMETRY_BASE_URL", "http://router:8080").rstrip("/")
START_TS = time.time()
START_ISO = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(START_TS))
START_US = int(START_TS * 1_000_000)


def telemetry_dirs() -> dict[str, str]:
    dirs = {
        "logs": os.path.join(OUTPUT_ROOT, "logs"),
        "traces": os.path.join(OUTPUT_ROOT, "traces"),
        "metrics": os.path.join(OUTPUT_ROOT, "metrics"),
    }
    for directory in dirs.values():
        os.makedirs(directory, exist_ok=True)
    return dirs


def atomic_write_json(path: str, payload: dict[str, Any]) -> None:
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    os.replace(tmp, path)


def http_json(url: str, method: str = "GET", payload: dict[str, Any] | None = None, headers: dict[str, str] | None = None) -> tuple[bool, Any]:
    req_headers = {"Accept": "application/json"}
    data = None
    if payload is not None:
        req_headers["Content-Type"] = "application/json"
        data = json.dumps(payload).encode("utf-8")
    if headers:
        req_headers.update(headers)

    try:
        req = urllib.request.Request(url, data=data, headers=req_headers, method=method)
        with urllib.request.urlopen(req, timeout=20) as response:
            body = response.read()
            return True, json.loads(body.decode("utf-8")) if body else {}
    except Exception as exc:
        return False, repr(exc)


def export_logs(dirs: dict[str, str]) -> None:
    limit = env_int("TELEMETRY_LOGS_LIMIT", 0, 0)
    batch_size = min(env_int("TELEMETRY_LOGS_BATCH_SIZE", 5000, 1), 10000)
    size = batch_size if limit == 0 else min(batch_size, limit)
    query = {
        "size": size,
        "sort": [{"observedTimestamp": {"order": "desc"}}],
        "query": {
            "range": {
                "observedTimestamp": {
                    "gte": START_ISO,
                    "lte": "now",
                }
            }
        },
    }
    ok, payload = http_json(f"{BASE_URL}/opensearch/otel/_search", method="POST", payload=query)
    if ok:
        atomic_write_json(
            os.path.join(dirs["logs"], "logs.json"),
            {"cumulative_from": START_ISO, "exported_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "payload": payload},
        )
    else:
        log(f"logs export failed: {payload!r}")


def export_traces(dirs: dict[str, str]) -> None:
    ok, services = http_json(f"{BASE_URL}/jaeger/ui/api/services")
    if not ok:
        log(f"trace service discovery failed: {services!r}")
        return

    traces: dict[str, Any] = {}
    limit = env_int("TELEMETRY_TRACE_LIMIT", 5000, 1)
    service_names = services.get("data", []) if isinstance(services, dict) else []
    for service in service_names:
        params = urllib.parse.urlencode({"service": service, "limit": limit, "start": START_US})
        ok, payload = http_json(f"{BASE_URL}/jaeger/ui/api/traces?{params}")
        if ok:
            traces[str(service)] = payload

    atomic_write_json(
        os.path.join(dirs["traces"], "traces.json"),
        {"cumulative_from": START_ISO, "exported_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "traces": traces},
    )


def export_metrics(dirs: dict[str, str]) -> None:
    params = urllib.parse.urlencode(
        {
            "query": os.getenv("TELEMETRY_METRICS_QUERY", '{__name__!=""}'),
            "start": str(int(START_TS)),
            "end": str(int(time.time())),
            "step": os.getenv("TELEMETRY_METRICS_STEP", "15s"),
        }
    )
    url = f"{BASE_URL}/grafana/api/datasources/proxy/uid/webstore-metrics/api/v1/query_range?{params}"
    ok, payload = http_json(url)
    if ok:
        atomic_write_json(
            os.path.join(dirs["metrics"], "metrics.json"),
            {"cumulative_from": START_ISO, "exported_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "payload": payload},
        )
    else:
        log(f"metrics export failed: {payload!r}")


def export_once() -> None:
    dirs = telemetry_dirs()
    export_logs(dirs)
    export_traces(dirs)
    export_metrics(dirs)


def export_loop() -> None:
    log(f"enabled base={BASE_URL} interval={INTERVAL}s output={OUTPUT_ROOT}")
    while True:
        started = time.time()
        try:
            export_once()
        except Exception as exc:
            log(f"iteration failed: {exc!r}")
        time.sleep(max(1.0, INTERVAL - (time.time() - started)))


def main() -> None:
    if not ENABLED:
        log("disabled by TELEMETRY_EXPORT_ENABLED")
        return
    thread = threading.Thread(target=export_loop, name="otel-telemetry-exporter", daemon=True)
    thread.start()


if __name__ == "__main__":
    main()
