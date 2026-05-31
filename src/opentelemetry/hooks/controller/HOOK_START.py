#!/usr/bin/env python3
"""Start periodic OpenTelemetry telemetry export in the controller process.

This controller hook launches a lightweight background exporter when telemetry is
enabled. It periodically snapshots logs, traces, and metrics while kill chains run
so post-run analysis can correlate attacks with observable telemetry.
"""

from __future__ import annotations

import json
import os
import threading
import time
import tomllib
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


def config_file_candidates() -> list[str]:
    return [
        os.getenv("CONFIG_FILE", ""),
        os.getenv("ENV_FILE", ""),
        "/runtime/config.toml",
    ]


def runtime_config_values() -> dict[str, str]:
    for candidate in config_file_candidates():
        if not candidate:
            continue

        path = os.path.abspath(candidate)
        if not os.path.isfile(path):
            continue

        try:
            with open(path, "rb") as handle:
                data = tomllib.load(handle)
            table = data.get("config", data)
            if isinstance(table, dict):
                return {
                    str(key): str(value)
                    for key, value in table.items()
                    if not isinstance(value, dict)
                }
        except Exception as exc:
            log(f"could not read runtime config {path}: {exc!r}")

    return {}


def state_values() -> dict[str, object]:
    state_file = os.getenv("STATE_FILE")
    if not state_file:
        runtime_dir = os.getenv("RUNTIME_DIR", "/res/runtime/honeypotlab")
        state_file = os.path.join(runtime_dir, "generated", "lab-state.json")

    if not os.path.isfile(state_file):
        return {}

    try:
        with open(state_file, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        values = payload.get("values", {}) if isinstance(payload, dict) else {}
        return values if isinstance(values, dict) else {}
    except Exception as exc:
        log(f"could not read state file {state_file}: {exc!r}")
        return {}


def first_non_empty(*values: object) -> str:
    for value in values:
        text = "" if value is None else str(value).strip()
        if text:
            return text
    return ""


def resolve_base_url() -> str:
    explicit = os.getenv("TELEMETRY_BASE_URL", "").strip()
    if explicit:
        return explicit.rstrip("/")

    state = state_values()
    config = runtime_config_values()
    frontend_proxy_ip = first_non_empty(
        state.get("FRONTEND_PROXY_IP"),
        config.get("FRONTEND_PROXY_IP"),
        os.getenv("FRONTEND_PROXY_IP"),
        "172.18.0.200",
    )
    return f"http://{frontend_proxy_ip}:8080".rstrip("/")


def resolve_prometheus_base_url(base_url: str) -> str:
    explicit = os.getenv("TELEMETRY_PROMETHEUS_BASE_URL", "").strip()
    if explicit:
        return explicit.rstrip("/")
    return f"{base_url}/prometheus"


ENABLED = env_bool("TELEMETRY_EXPORT_ENABLED", True)
INTERVAL = env_int("TELEMETRY_EXPORT_INTERVAL_SEC", 60, 1)
READY_TIMEOUT = env_int("TELEMETRY_EXPORT_READY_TIMEOUT_SEC", 180, 0)
OUTPUT_ROOT = os.getenv("TELEMETRY_OUTPUT_ROOT", "/results/telemetry")
BASE_URL = resolve_base_url()
PROMETHEUS_BASE_URL = resolve_prometheus_base_url(BASE_URL)
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


def http_json(
    url: str,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[bool, Any]:
    """Fetch a URL expected to return JSON."""

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


def http_status_ok(url: str, *, timeout: int = 5) -> tuple[bool, str]:
    """Fetch a readiness URL and accept any 2xx response, JSON or not.

    Prometheus /-/ready returns text/plain, not JSON. Readiness checks should not
    use http_json(), otherwise a healthy endpoint is reported as JSONDecodeError.
    """

    try:
        req = urllib.request.Request(url, headers={"Accept": "*/*"}, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as response:
            status = int(getattr(response, "status", response.getcode()))
            body = response.read(256).decode("utf-8", errors="replace").strip()

        if 200 <= status < 300:
            return True, body
        return False, f"HTTP {status}: {body}"
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
            {
                "cumulative_from": START_ISO,
                "exported_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "payload": payload,
            },
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
        params = urllib.parse.urlencode(
            {
                "service": service,
                "limit": limit,
                "start": START_US,
            }
        )
        ok, payload = http_json(f"{BASE_URL}/jaeger/ui/api/traces?{params}")
        if ok:
            traces[str(service)] = payload

    atomic_write_json(
        os.path.join(dirs["traces"], "traces.json"),
        {
            "cumulative_from": START_ISO,
            "exported_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "traces": traces,
        },
    )


def metrics_query_urls(params: str) -> list[str]:
    urls = [
        f"{PROMETHEUS_BASE_URL}/api/v1/query_range?{params}",
        f"{BASE_URL}/grafana/api/datasources/proxy/uid/webstore-metrics/api/v1/query_range?{params}",
    ]

    deduped: list[str] = []
    for url in urls:
        if url not in deduped:
            deduped.append(url)
    return deduped


def export_metrics(dirs: dict[str, str]) -> None:
    params = urllib.parse.urlencode(
        {
            "query": os.getenv("TELEMETRY_METRICS_QUERY", '{__name__!=""}'),
            "start": str(int(START_TS)),
            "end": str(int(time.time())),
            "step": os.getenv("TELEMETRY_METRICS_STEP", "15s"),
        }
    )

    errors: list[str] = []
    for url in metrics_query_urls(params):
        ok, payload = http_json(url)
        if ok:
            atomic_write_json(
                os.path.join(dirs["metrics"], "metrics.json"),
                {
                    "cumulative_from": START_ISO,
                    "exported_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "source": url.split("?", 1)[0],
                    "payload": payload,
                },
            )
            return
        errors.append(f"{url.split('?', 1)[0]} -> {payload!r}")

    log("metrics export failed: " + "; ".join(errors))


def export_once() -> None:
    dirs = telemetry_dirs()
    export_logs(dirs)
    export_traces(dirs)
    export_metrics(dirs)


def wait_for_backend_readiness() -> None:
    """Avoid noisy exports while the frontend-proxy and telemetry backends start."""

    if READY_TIMEOUT <= 0:
        return

    deadline = time.time() + READY_TIMEOUT
    urls = [
        f"{BASE_URL}/opensearch",
        f"{BASE_URL}/jaeger/ui/api/services",
        f"{PROMETHEUS_BASE_URL}/-/ready",
    ]

    while time.time() < deadline:
        errors: list[str] = []

        for url in urls:
            ok, detail = http_status_ok(url)
            if not ok:
                errors.append(f"{url} -> {detail!r}")

        if not errors:
            log("telemetry backends are reachable")
            return

        log("waiting for telemetry backends: " + "; ".join(errors))
        time.sleep(5)

    log(f"telemetry backends were not fully ready after {READY_TIMEOUT}s; continuing anyway")


def export_loop() -> None:
    log(
        f"enabled base={BASE_URL} prometheus={PROMETHEUS_BASE_URL} "
        f"interval={INTERVAL}s output={OUTPUT_ROOT}"
    )
    wait_for_backend_readiness()

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