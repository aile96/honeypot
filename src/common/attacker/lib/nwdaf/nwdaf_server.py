#!/usr/bin/env python3
"""Run a tiny NWDAF-compatible test endpoint and register it with NRF."""

from __future__ import annotations

import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

try:
    from .nrf_register import env_bool, env_int, wait_and_register
except ImportError:  # pragma: no cover - supports direct execution by path.
    from nwdaf.nrf_register import env_bool, env_int, wait_and_register


class Handler(BaseHTTPRequestHandler):
    """HTTP handler used by probes and by dummy NF service endpoints."""

    server_version = "free5gc-test-nwdaf/1.0"

    def log_message(self, fmt: str, *args: object) -> None:
        """Send access logs to stdout."""
        print(f"{self.client_address[0]} - {fmt % args}", flush=True)

    def write_json(self, status: int, payload: dict[str, object]) -> None:
        """Write a JSON response."""
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler naming.
        """Handle health checks and dummy NF service calls."""
        self.write_json(
            200,
            {
                "status": "ok",
                "nfType": os.getenv("NF_TYPE", "NWDAF"),
                "path": self.path,
            },
        )

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler naming.
        """Accept dummy POST calls for service compatibility tests."""
        self.write_json(200, {"status": "accepted", "path": self.path})

    def do_PUT(self) -> None:  # noqa: N802 - stdlib handler naming.
        """Accept dummy PUT calls for service compatibility tests."""
        self.write_json(200, {"status": "accepted", "path": self.path})


def register_on_start() -> None:
    """Register this pod as NWDAF after the HTTP endpoint starts."""
    wait_and_register(
        os.getenv("NF_TYPE", "NWDAF"),
        env_int("REGISTER_TIMEOUT_SECONDS", 180),
        env_int("REGISTER_RETRY_SECONDS", 3),
    )


def main() -> int:
    """Start the server and optionally self-register as NWDAF."""
    host = os.getenv("NWDAF_HOST", "0.0.0.0")
    port = env_int("NWDAF_PORT", 8080)

    server = ThreadingHTTPServer((host, port), Handler)
    print(f"NWDAF test service listening on {host}:{port}", flush=True)

    if env_bool("REGISTER_ON_START", True):
        # Registration is done in a side thread so probes can pass while NRF is starting.
        threading.Thread(target=register_on_start, daemon=True).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
