#!/usr/bin/env python3
"""
Best-effort cleanup for a CALDERA Sandcat child agent.

The script is intended to run as the last KC2 child step. In async mode it
spawns a detached cleanup worker, waits a few seconds so Sandcat can report the
last ability result, then:
  1. deletes the fixed PAW from CALDERA via /api/rest;
  2. terminates the local Sandcat process from /tmp/sandcat.pid.

Deleting the PAW alone is not enough: a live Sandcat process will beacon again
and re-create the agent entry in CALDERA.
"""

from __future__ import annotations

import argparse
import http.client
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlparse


DEFAULT_PID_FILE = "/tmp/sandcat.pid"
DEFAULT_LOG_FILE = "/tmp/KCData/KC2/analysis/caldera-agent-cleanup.log"


def log(message: str) -> None:
    print(f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} {message}", flush=True)


def normalize_url(url: str) -> str:
    if not url:
        raise ValueError("CALDERA URL is empty")
    return url.rstrip("/")


def delete_agent(caldera_url: str, api_key: str, paw: str) -> None:
    parsed = urlparse(normalize_url(caldera_url))
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("CALDERA URL must start with http:// or https://")
    if not parsed.hostname:
        raise ValueError("CALDERA URL has no hostname")

    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    base_path = parsed.path.rstrip("/")
    path = f"{base_path}/api/rest" if base_path else "/api/rest"

    body = json.dumps({"index": "agents", "paw": paw}).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Content-Length": str(len(body)),
    }
    if api_key:
        headers["KEY"] = api_key

    conn_cls = http.client.HTTPSConnection if parsed.scheme == "https" else http.client.HTTPConnection
    conn = conn_cls(parsed.hostname, port, timeout=20)
    try:
        conn.request("DELETE", path, body=body, headers=headers)
        response = conn.getresponse()
        response_body = response.read().decode("utf-8", errors="replace")
        log(f"CALDERA delete agent response: {response.status} {response.reason}")
        if response_body:
            log(response_body)
        if response.status < 200 or response.status >= 300:
            raise RuntimeError(f"CALDERA delete agent failed with status {response.status}")
    finally:
        conn.close()


def kill_sandcat(pid_file: str) -> None:
    path = Path(pid_file)
    if not path.is_file():
        log(f"Sandcat pid file not found: {pid_file}")
        return

    raw_pid = path.read_text(encoding="utf-8").strip()
    if not raw_pid:
        log(f"Sandcat pid file is empty: {pid_file}")
        return

    pid = int(raw_pid)
    try:
        os.kill(pid, signal.SIGTERM)
        log(f"Sent SIGTERM to Sandcat pid {pid}")
    except ProcessLookupError:
        log(f"Sandcat pid {pid} is not running")
    except PermissionError as exc:
        raise RuntimeError(f"Cannot terminate Sandcat pid {pid}: {exc}") from exc


def spawn_async(args: argparse.Namespace) -> None:
    cmd = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--caldera-url",
        args.caldera_url,
        "--paw",
        args.paw,
        "--pid-file",
        args.pid_file,
        "--delay",
        str(args.delay),
    ]
    if args.api_key:
        cmd.extend(["--api-key", args.api_key])
    if args.no_kill:
        cmd.append("--no-kill")

    log_file = Path(args.log_file)
    log_file.parent.mkdir(parents=True, exist_ok=True)
    fh = log_file.open("ab")
    subprocess.Popen(
        cmd,
        stdin=subprocess.DEVNULL,
        stdout=fh,
        stderr=subprocess.STDOUT,
        start_new_session=True,
        close_fds=True,
    )
    log(f"Scheduled async CALDERA agent cleanup; log={log_file}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--caldera-url", default=os.environ.get("CALDERA_URL", "http://caldera:8888"))
    parser.add_argument("--api-key", default=os.environ.get("CALDERA_API_KEY", "ADMIN123"))
    parser.add_argument("--paw", default=os.environ.get("SANDCAT_PAW", ""))
    parser.add_argument("--pid-file", default=os.environ.get("SANDCAT_PID_FILE", DEFAULT_PID_FILE))
    parser.add_argument("--delay", type=int, default=int(os.environ.get("KC2_AGENT_CLEANUP_DELAY", "10")))
    parser.add_argument("--log-file", default=os.environ.get("KC2_AGENT_CLEANUP_LOG", DEFAULT_LOG_FILE))
    parser.add_argument("--async", dest="run_async", action="store_true")
    parser.add_argument("--no-kill", action="store_true", help="Delete the CALDERA agent entry but do not stop local Sandcat.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if not args.paw:
        raise ValueError("Missing PAW. Set SANDCAT_PAW or pass --paw.")

    if args.run_async:
        spawn_async(args)
        return 0

    if args.delay > 0:
        log(f"Waiting {args.delay}s before cleanup")
        time.sleep(args.delay)

    delete_agent(args.caldera_url, args.api_key, args.paw)

    if not args.no_kill:
        kill_sandcat(args.pid_file)

    log("CALDERA agent cleanup completed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
