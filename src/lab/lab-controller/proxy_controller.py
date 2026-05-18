#!/usr/bin/env python3
"""Expose a single lab proxy endpoint from the controller container.

The proxy keeps the host surface stable: start.sh publishes only this process.
It provides health/status endpoints and forwards a small set of well-known HTTP
services. Additional services can be added without changing host port bindings.
"""

from __future__ import annotations

import http.client
import json
import os
import select
import socket
import socketserver
import time
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlsplit


PORT = int(os.getenv("CONTROLLER_PROXY_CONTAINER_PORT", "18080"))
LAB_NAME = os.getenv("LAB_NAME", os.getenv("CLUSTER_PROFILE", "honeypotlab"))
CALDERA_SERVER = os.getenv("CALDERA_SERVER", "caldera")
CALDERA_PORT = int(os.getenv("CALDERA_PORT", "8888"))
FRONTEND_PROXY_PORT = int(os.getenv("FRONTEND_PROXY_PORT", "8080"))
STATE_FILE = os.getenv("STATE_FILE", os.path.join(os.getenv("RUNTIME_DIR", "/res/runtime/honeypotlab"), "generated", "lab-state.json"))
CALDERA_ABSOLUTE_PREFIXES = (
    "/assets/",
    "/api/",
    "/plugin/",
    "/plugins/",
    "/socket.io/",
    "/file/",
    "/gui/",
    "/download/",
    "/enter",
    "/login",
    "/logout",
    "/favicon.ico",
)


def state_value(name: str, default: str = "") -> str:
    """Read a dynamic value from the pipeline state file."""
    try:
        with open(STATE_FILE, encoding="utf-8") as handle:
            data = json.load(handle)
        values = data.get("values", data)
        value = values.get(name, default) if isinstance(values, dict) else default
        return str(value) if value is not None else default
    except Exception:
        return default


def state_object(name: str, default: object | None = None) -> object | None:
    """Read a dynamic object from the pipeline state file."""
    try:
        with open(STATE_FILE, encoding="utf-8") as handle:
            data = json.load(handle)
        values = data.get("values", data)
        if not isinstance(values, dict):
            return default
        return values.get(name, default)
    except Exception:
        return default


def kube_api_proxy_target() -> tuple[str, int] | None:
    """Return the Kubernetes API passthrough target discovered by the pipeline."""
    target = state_object("kube_api_proxy_target", {})
    if not isinstance(target, dict) or not target.get("enabled"):
        return None

    host = str(target.get("host", "")).strip()
    try:
        port = int(target.get("port", 0))
    except (TypeError, ValueError):
        return None

    if not host or port <= 0:
        return None
    return host, port


def routes() -> dict[str, tuple[str, int, bool]]:
    """Return current proxy routes, including values discovered after startup."""
    caldera_target = (
        os.getenv("CALDERA_SERVER", CALDERA_SERVER),
        int(os.getenv("CALDERA_PORT", str(CALDERA_PORT))),
    )
    current = {
        "/caldera/": (*caldera_target, True),
        "/registry/": (os.getenv("REGISTRY_NAME", "registry"), int(os.getenv("REGISTRY_PORT", "5000")), True),
    }
    for prefix in CALDERA_ABSOLUTE_PREFIXES:
        current[prefix] = (*caldera_target, False)
    frontend_proxy_ip = state_value("FRONTEND_PROXY_IP", os.getenv("FRONTEND_PROXY_IP", ""))
    if frontend_proxy_ip:
        current["/frontend/"] = (frontend_proxy_ip, FRONTEND_PROXY_PORT, True)
    return current


def log(*parts: object) -> None:
    print(time.strftime("[%Y-%m-%d %H:%M:%S]", time.localtime()), "proxy:", *parts, flush=True)


class ThreadingHTTPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def handle(self) -> None:
        if self._looks_like_tls():
            self._tunnel_kube_api()
            return
        super().handle()

    def log_message(self, fmt: str, *args: object) -> None:
        log(fmt % args)

    def _write_json(self, status: int, payload: dict[str, object]) -> None:
        body = json.dumps(payload, indent=2).encode("utf-8") + b"\n"
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _looks_like_tls(self) -> bool:
        try:
            first = self.request.recv(1, socket.MSG_PEEK)
        except OSError:
            return False
        return first == b"\x16"

    def _tunnel_kube_api(self) -> None:
        target = kube_api_proxy_target()
        if target is None:
            log("dropping Kubernetes API TLS connection: proxy target not ready")
            return

        upstream: socket.socket | None = None
        host, port = target
        try:
            upstream = socket.create_connection((host, port), timeout=30)
            self.request.settimeout(None)
            upstream.settimeout(None)
            self._relay_streams(self.request, upstream)
        except OSError as exc:
            log(f"Kubernetes API TLS tunnel error to {host}:{port}: {exc!r}")
        finally:
            if upstream is not None:
                try:
                    upstream.close()
                except OSError:
                    pass

    def _relay_streams(self, client: socket.socket, upstream: socket.socket) -> None:
        sockets = [client, upstream]
        peers = {client: upstream, upstream: client}
        while True:
            try:
                readable, _, exceptional = select.select(sockets, [], sockets, 300)
            except OSError:
                return
            if exceptional or not readable:
                return
            for source in readable:
                try:
                    data = source.recv(65536)
                except OSError:
                    return
                if not data:
                    return
                try:
                    peers[source].sendall(data)
                except OSError:
                    return

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/caldera":
            self.send_response(302)
            self.send_header("Location", "/caldera/")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if self.path in {"/healthz", "/status"}:
            self._write_json(
                200,
                {
                    "ok": True,
                    "lab_name": LAB_NAME,
                    "routes": sorted(routes().keys()),
                    "kubernetes_api_proxy": kube_api_proxy_target() is not None,
                },
            )
            return
        self._proxy()

    def do_POST(self) -> None:  # noqa: N802
        self._proxy()

    def do_PUT(self) -> None:  # noqa: N802
        self._proxy()

    def do_DELETE(self) -> None:  # noqa: N802
        self._proxy()

    def _proxy(self) -> None:
        route_prefix = ""
        target: tuple[str, int, bool] | None = None
        for prefix, candidate in routes().items():
            if self.path.startswith(prefix):
                route_prefix = prefix
                target = candidate
                break
        if target is None:
            host = os.getenv("CALDERA_SERVER", CALDERA_SERVER)
            port = int(os.getenv("CALDERA_PORT", str(CALDERA_PORT)))
            route_prefix = ""
            strip_prefix = False
        else:
            host, port, strip_prefix = target

        parsed = urlsplit(self.path)
        upstream_path = parsed.path
        if strip_prefix:
            upstream_path = parsed.path[len(route_prefix) - 1 :] or "/"
        if parsed.query:
            upstream_path += "?" + parsed.query

        body = None
        length = self.headers.get("Content-Length")
        if length:
            body = self.rfile.read(int(length))

        headers = {key: value for key, value in self.headers.items() if key.lower() not in {"host", "connection", "content-length"}}
        headers["Host"] = f"{host}:{port}"
        try:
            conn = http.client.HTTPConnection(host, port, timeout=30)
            conn.request(self.command, upstream_path, body=body, headers=headers)
            response = conn.getresponse()
            payload = response.read()
        except Exception as exc:
            self._write_json(502, {"ok": False, "error": repr(exc), "upstream": f"{host}:{port}"})
            return

        self.send_response(response.status, response.reason)
        for key, value in response.getheaders():
            header_name = key.lower()
            if header_name in {"connection", "transfer-encoding"}:
                continue
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def main() -> int:
    log(f"starting lab proxy for {LAB_NAME} on 0.0.0.0:{PORT}")
    with ThreadingHTTPServer(("0.0.0.0", PORT), Handler) as server:
        server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
