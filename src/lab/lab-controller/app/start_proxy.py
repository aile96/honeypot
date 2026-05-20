#!/usr/bin/env python3
"""Expose a single lab proxy endpoint from the controller container.

The proxy is disabled in HOST_SOCKET=true mode. In internal Docker mode it keeps
only explicitly configured dynamic HTTP tunnels and the Kubernetes TLS tunnel.
Default HTTP routes to CALDERA, registry, and CALDERA absolute paths are disabled
until an authenticated tunnel request selects an upstream.
"""

from __future__ import annotations

import json
import os
import select
import socket
import socketserver
import time
import tomllib
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit


CONFIG_FILE = Path(os.getenv("CONFIG_FILE", "/runtime/config.toml"))


def load_config() -> dict[str, Any]:
    if not CONFIG_FILE.is_file():
        raise SystemExit(f"proxy: runtime config not found: {CONFIG_FILE}")
    with CONFIG_FILE.open("rb") as handle:
        data = tomllib.load(handle)
    table = data.get("config", data)
    if not isinstance(table, dict):
        raise SystemExit(f"proxy: runtime config must contain a [config] table: {CONFIG_FILE}")
    return {str(key): value for key, value in table.items() if not isinstance(value, dict)}


def config_value(config: dict[str, Any], name: str, default: Any = None) -> Any:
    value = config.get(name, default)
    if value is None or str(value).strip() == "":
        if default is None:
            raise SystemExit(f"proxy: missing required config value: {name}")
        return default
    return value


def config_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in {"", "0", "false", "no", "off"}


CONFIG = load_config()
PORT = int(config_value(CONFIG, "CONTROLLER_PROXY_CONTAINER_PORT"))
LAB_NAME = str(config_value(CONFIG, "LAB_NAME", config_value(CONFIG, "CLUSTER_PROFILE", "honeypotlab")))
HOST_SOCKET = config_bool(config_value(CONFIG, "HOST_SOCKET", False))
PROXY_TUNNEL_USER = str(config_value(CONFIG, "PROXY_TUNNEL_USER", "lab"))
PROXY_TUNNEL_PASS = str(config_value(CONFIG, "PROXY_TUNNEL_PASS", "lab"))
STATE_FILE = str(config_value(CONFIG, "STATE_FILE"))

# Dynamic routes are registered at runtime via authenticated POST requests. The
# selected tunnel becomes the default HTTP upstream at "/".
DYNAMIC_ROUTES: dict[str, tuple[str, int, bool]] = {}
HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "proxy-connection",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}


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
    """Return current proxy routes.

    Default routes are intentionally disabled. Only authenticated dynamic HTTP
    tunnels registered during runtime are exposed.
    """
    return dict(DYNAMIC_ROUTES)


def register_dynamic_tunnel(name: str, port: int) -> str:
    """Register the selected upstream as the default HTTP proxy route."""
    DYNAMIC_ROUTES["/"] = (name, port, False)
    return "/"


def matching_route(path: str) -> tuple[str, tuple[str, int, bool]] | None:
    """Return the most specific dynamic route matching *path*."""
    matches = [
        (prefix, target)
        for prefix, target in routes().items()
        if path.startswith(prefix)
    ]
    if not matches:
        return None
    return max(matches, key=lambda item: len(item[0]))


def log(*parts: object) -> None:
    print(
        time.strftime("[%Y-%m-%d %H:%M:%S]", time.localtime()),
        "proxy:",
        *parts,
        flush=True,
    )


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
        if self._configure_dynamic_tunnel():
            return
        self._proxy()

    def do_PUT(self) -> None:  # noqa: N802
        self._proxy()

    def do_DELETE(self) -> None:  # noqa: N802
        self._proxy()

    def do_PATCH(self) -> None:  # noqa: N802
        self._proxy()

    def do_OPTIONS(self) -> None:  # noqa: N802
        self._proxy()

    def do_HEAD(self) -> None:  # noqa: N802
        self._proxy(send_body=False)

    def _read_json_body(self) -> dict[str, object]:
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length <= 0:
            return {}
        try:
            payload = self.rfile.read(length)
            data = json.loads(payload.decode("utf-8"))
        except Exception:
            return {}
        return data if isinstance(data, dict) else {}

    def _configure_dynamic_tunnel(self) -> bool:
        parts = [part for part in urlsplit(self.path).path.split("/") if part]
        if parts and parts[0] == "_tunnel":
            parts = parts[1:]
        if len(parts) != 2:
            return False

        name, port_text = parts
        if not port_text.isdigit():
            return False

        payload = self._read_json_body()
        if (
            str(payload.get("username", "")) != PROXY_TUNNEL_USER
            or str(payload.get("password", "")) != PROXY_TUNNEL_PASS
        ):
            self._write_json(403, {"ok": False, "error": "invalid credentials"})
            return True

        port = int(port_text)
        if port < 1 or port > 65535:
            self._write_json(400, {"ok": False, "error": "invalid port"})
            return True

        route = register_dynamic_tunnel(name, port)
        self._write_json(
            200,
            {"ok": True, "route": route, "upstream": f"{name}:{port}"},
        )
        return True

    def _external_host(self) -> str:
        return self.headers.get("Host", "").strip()

    def _external_proto(self) -> str:
        proto = self.headers.get("X-Forwarded-Proto", "").split(",", 1)[0].strip()
        return proto or "http"

    def _external_port(self) -> str | None:
        forwarded_port = self.headers.get("X-Forwarded-Port", "").split(",", 1)[0].strip()
        if forwarded_port:
            return forwarded_port

        host = self._external_host()
        if not host:
            return None

        try:
            port = urlsplit(f"//{host}").port
        except ValueError:
            port = None
        if port is not None:
            return str(port)
        return "443" if self._external_proto() == "https" else "80"

    def _connection_header_names(self) -> set[str]:
        names: set[str] = set()
        for value in self.headers.get_all("Connection", []):
            names.update(part.strip().lower() for part in value.split(",") if part.strip())
        return names

    def _read_proxy_body(self) -> bytes | None:
        length_header = self.headers.get("Content-Length")
        if length_header is None:
            return None

        try:
            length = int(length_header)
        except ValueError as exc:
            raise ValueError("invalid Content-Length") from exc
        if length < 0:
            raise ValueError("invalid Content-Length")
        return self.rfile.read(length)

    def _forward_headers(self, upstream_host: str, upstream_port: int, body: bytes | None) -> dict[str, str]:
        external_host = self._external_host() or f"{upstream_host}:{upstream_port}"
        external_proto = self._external_proto()
        external_port = self._external_port() or str(upstream_port)
        client_ip = self.client_address[0] if self.client_address else ""
        connection_header_names = self._connection_header_names()

        headers = {}
        for key, value in self.headers.items():
            header_name = key.lower()
            if (
                header_name == "host"
                or header_name == "content-length"
                or header_name in HOP_BY_HOP_HEADERS
                or header_name in connection_header_names
            ):
                continue
            headers[key] = value

        headers["Host"] = external_host
        headers["X-Forwarded-Host"] = external_host
        headers["X-Forwarded-Proto"] = external_proto
        headers["X-Forwarded-Port"] = external_port
        forwarded_for = self.headers.get("X-Forwarded-For", "").strip()
        if forwarded_for and client_ip:
            headers["X-Forwarded-For"] = f"{forwarded_for}, {client_ip}"
        elif client_ip:
            headers["X-Forwarded-For"] = client_ip
        elif forwarded_for:
            headers["X-Forwarded-For"] = forwarded_for
        headers["X-Real-IP"] = client_ip
        headers["Connection"] = "close"
        if body is not None:
            headers["Content-Length"] = str(len(body))
        return headers

    def _rewrite_location(self, value: str, upstream_host: str, upstream_port: int) -> str:
        parsed = urlsplit(value)
        if not parsed.scheme or not parsed.netloc:
            return value

        try:
            location_port = parsed.port
        except ValueError:
            return value

        location_host = (parsed.hostname or "").lower()
        upstream_hosts = {
            upstream_host.lower(),
            "localhost",
            "127.0.0.1",
            "0.0.0.0",
        }
        if location_host not in upstream_hosts or location_port != upstream_port:
            return value

        external_host = self._external_host() or f"{upstream_host}:{upstream_port}"
        return urlunsplit(
            (
                self._external_proto(),
                external_host,
                parsed.path,
                parsed.query,
                parsed.fragment,
            )
        )

    def _is_upgrade_request(self) -> bool:
        upgrade = self.headers.get("Upgrade", "").strip().lower()
        connection_tokens = self._connection_header_names()
        return upgrade == "websocket" and "upgrade" in connection_tokens

    def _proxy_upgrade(self, match: tuple[str, tuple[str, int, bool]]) -> None:
        route_prefix, target = match
        host, port, strip_prefix = target
        parsed = urlsplit(self.path)
        upstream_path = parsed.path
        if strip_prefix:
            upstream_path = parsed.path[len(route_prefix) - 1 :] or "/"
        if parsed.query:
            upstream_path += "?" + parsed.query

        try:
            body = self._read_proxy_body()
        except ValueError as exc:
            self._write_json(400, {"ok": False, "error": str(exc)})
            return

        external_host = self._external_host() or f"{host}:{port}"
        external_proto = self._external_proto()
        external_port = self._external_port() or str(port)
        client_ip = self.client_address[0] if self.client_address else ""
        connection_header_names = self._connection_header_names()

        headers = {}
        for key, value in self.headers.items():
            header_name = key.lower()
            if header_name == "host" or header_name == "content-length":
                continue
            if header_name in HOP_BY_HOP_HEADERS and header_name not in {"connection", "upgrade"}:
                continue
            if header_name in connection_header_names and header_name != "upgrade":
                continue
            headers[key] = value

        headers["Host"] = external_host
        headers["Connection"] = self.headers.get("Connection", "Upgrade")
        headers["Upgrade"] = self.headers.get("Upgrade", "websocket")
        headers["X-Forwarded-Host"] = external_host
        headers["X-Forwarded-Proto"] = external_proto
        headers["X-Forwarded-Port"] = external_port
        forwarded_for = self.headers.get("X-Forwarded-For", "").strip()
        if forwarded_for and client_ip:
            headers["X-Forwarded-For"] = f"{forwarded_for}, {client_ip}"
        elif client_ip:
            headers["X-Forwarded-For"] = client_ip
        elif forwarded_for:
            headers["X-Forwarded-For"] = forwarded_for
        headers["X-Real-IP"] = client_ip
        if body is not None:
            headers["Content-Length"] = str(len(body))

        upstream: socket.socket | None = None
        tunnel_started = False
        try:
            upstream = socket.create_connection((host, port), timeout=30)
            request_head = f"{self.command} {upstream_path} HTTP/1.1\r\n".encode("iso-8859-1")
            for key, value in headers.items():
                request_head += f"{key}: {value}\r\n".encode("iso-8859-1")
            request_head += b"\r\n"
            upstream.sendall(request_head)
            if body:
                upstream.sendall(body)

            self.request.settimeout(None)
            upstream.settimeout(None)
            tunnel_started = True
            self._relay_streams(self.request, upstream)
        except Exception as exc:
            log(f"websocket upgrade error to {host}:{port}: {exc!r}")
            if not tunnel_started:
                self._write_json(
                    502,
                    {"ok": False, "error": repr(exc), "upstream": f"{host}:{port}"},
                )
        finally:
            if upstream is not None:
                try:
                    upstream.close()
                except OSError:
                    pass

    def _proxy(self, send_body: bool = True) -> None:
        parsed = urlsplit(self.path)
        match = matching_route(parsed.path)
        if match is None:
            self._write_json(
                404,
                {
                    "ok": False,
                    "error": "no matching proxy route",
                    "path": parsed.path,
                    "available_routes": sorted(routes().keys()),
                },
            )
            return

        if self._is_upgrade_request():
            self._proxy_upgrade(match)
            return

        route_prefix, target = match
        host, port, strip_prefix = target
        upstream_path = parsed.path
        if strip_prefix:
            upstream_path = parsed.path[len(route_prefix) - 1 :] or "/"
        if parsed.query:
            upstream_path += "?" + parsed.query

        try:
            body = self._read_proxy_body()
        except ValueError as exc:
            self._write_json(400, {"ok": False, "error": str(exc)})
            return

        headers = self._forward_headers(host, port, body)
        conn = None
        try:
            import http.client

            conn = http.client.HTTPConnection(host, port, timeout=30)
            conn.request(self.command, upstream_path, body=body, headers=headers)
            response = conn.getresponse()
            payload = response.read()
            response_headers = response.getheaders()
        except Exception as exc:
            log(f"HTTP proxy error to {host}:{port}: {exc!r}")
            self._write_json(
                502,
                {"ok": False, "error": repr(exc), "upstream": f"{host}:{port}"},
            )
            return
        finally:
            if conn is not None:
                conn.close()

        self.send_response(response.status, response.reason)
        response_connection_header_names: set[str] = set()
        for key, value in response_headers:
            if key.lower() == "connection":
                response_connection_header_names.update(
                    part.strip().lower() for part in value.split(",") if part.strip()
                )
        for key, value in response_headers:
            header_name = key.lower()
            if (
                header_name == "content-length"
                or header_name in HOP_BY_HOP_HEADERS
                or header_name in response_connection_header_names
            ):
                continue
            if header_name == "location":
                value = self._rewrite_location(value, host, port)
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(payload) if send_body else 0))
        self.end_headers()
        if send_body:
            self.wfile.write(payload)


def main() -> int:
    if HOST_SOCKET:
        log("HOST_SOCKET=true; proxy disabled.")
        return 0
    log(f"starting lab proxy for {LAB_NAME} on 0.0.0.0:{PORT}")
    with ThreadingHTTPServer(("0.0.0.0", PORT), Handler) as server:
        server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
