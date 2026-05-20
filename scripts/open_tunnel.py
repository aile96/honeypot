#!/usr/bin/env python3

"""
Open a lab proxy tunnel through the controller proxy.

By default the script reads configuration.conf and the saved runtime metadata in
res/runtime/<LAB_NAME>/info, then opens a tunnel to the configured Caldera
container on port 8888.

Default usage from the repository root:

    python3 scripts/open_tunnel.py

Equivalent explicit usage:

    python3 scripts/open_tunnel.py -n honeypotlab2 -c caldera -p 8888

The old positional form is still accepted for compatibility:

    python3 scripts/open_tunnel.py <proxy_url> <name> <port> <username> <password>
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python < 3.11 fallback
    import tomli as tomllib  # type: ignore[no-redef]


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_FILE = PROJECT_ROOT / "configuration.conf"
DEFAULT_RUNTIME_ROOT = PROJECT_ROOT / "res" / "runtime"
DEFAULT_PROXY_USER = "lab"
DEFAULT_PROXY_PASS = "lab"
DEFAULT_CALDERA_CONTAINER = "caldera"
DEFAULT_CALDERA_PORT = "8888"


@dataclass(frozen=True)
class TunnelOptions:
    proxy_url: str
    name: str
    port: str
    username: str
    password: str
    lab_name: str | None = None
    info_path: Path | None = None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Open an authenticated HTTP tunnel through the lab controller proxy. "
            "Without arguments, the script uses configuration.conf and "
            "res/runtime/<LAB_NAME>/info, and targets Caldera on port 8888."
        )
    )

    parser.add_argument(
        "-n",
        "--name",
        dest="lab_name",
        help="Lab name. Defaults to LAB_NAME from configuration.conf.",
    )
    parser.add_argument(
        "-p",
        "--port",
        help="Internal target port to forward. Defaults to the configured Caldera port, usually 8888.",
    )
    parser.add_argument(
        "-c",
        "--container",
        help="Internal container/service name to forward to. Defaults to the configured Caldera container.",
    )
    parser.add_argument(
        "--config",
        default=str(DEFAULT_CONFIG_FILE),
        help="Path to configuration.conf. Defaults to ./configuration.conf.",
    )
    parser.add_argument(
        "--proxy-url",
        help=(
            "Override the controller proxy URL, for example http://127.0.0.1:18080. "
            "If omitted, it is read from res/runtime/<LAB_NAME>/info."
        ),
    )
    parser.add_argument(
        "-u",
        "--username",
        help="Tunnel username. Defaults to PROXY_TUNNEL_USER from configuration.conf.",
    )
    parser.add_argument(
        "--password",
        help="Tunnel password. Defaults to PROXY_TUNNEL_PASS from configuration.conf.",
    )
    parser.add_argument(
        "legacy",
        nargs="*",
        metavar="LEGACY",
        help=argparse.SUPPRESS,
    )

    return parser.parse_args()


def load_config(config_path: Path) -> dict[str, Any]:
    if not config_path.is_file():
        raise ValueError(f"configuration file not found: {config_path}")
    try:
        return tomllib.loads(config_path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"invalid TOML in {config_path}: {exc}") from exc


def validate_port(port: str) -> str:
    try:
        value = int(str(port))
    except ValueError as exc:
        raise ValueError(f"invalid port {port!r}: port must be an integer") from exc

    if value < 1 or value > 65535:
        raise ValueError(f"invalid port {port!r}: port must be between 1 and 65535")

    return str(value)


def require_mapping(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{name} is missing or is not a TOML table")
    return value


def str_config(value: Any, default: str) -> str:
    if value is None:
        return default
    text = str(value).strip()
    return text if text else default


def config_defaults(config: dict[str, Any]) -> dict[str, str]:
    lab = require_mapping(config.get("lab"), "[lab]")
    target_name = str_config(lab.get("CLUSTER_TARGET"), "")
    targets = config.get("targets", {})
    target: dict[str, Any] = {}
    if isinstance(targets, dict) and target_name:
        target = targets.get(target_name, {}) if isinstance(targets.get(target_name, {}), dict) else {}

    return {
        "lab_name": str_config(lab.get("LAB_NAME"), "honeypotlab"),
        "username": str_config(lab.get("PROXY_TUNNEL_USER"), DEFAULT_PROXY_USER),
        "password": str_config(lab.get("PROXY_TUNNEL_PASS"), DEFAULT_PROXY_PASS),
        "container": str_config(target.get("CALDERA_SERVER"), DEFAULT_CALDERA_CONTAINER),
        "port": str_config(target.get("CALDERA_PORT"), DEFAULT_CALDERA_PORT),
    }


def runtime_info_path(lab_name: str) -> Path:
    return DEFAULT_RUNTIME_ROOT / lab_name / "info"


def proxy_url_from_info(info_path: Path) -> str:
    if not info_path.is_file():
        raise ValueError(
            f"runtime info file not found: {info_path}. Start the lab first, "
            "or pass --proxy-url explicitly."
        )

    try:
        info = json.loads(info_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON in runtime info file {info_path}: {exc}") from exc

    controller_proxy = info.get("controller_proxy")
    if not isinstance(controller_proxy, dict):
        raise ValueError(f"runtime info file {info_path} does not contain controller_proxy metadata")

    if not controller_proxy.get("exposed", False):
        raise ValueError(
            "controller proxy is not exposed to the host. Set EXPOSE_TO_HOST=true "
            "or pass --proxy-url to a reachable proxy endpoint."
        )

    host = str_config(controller_proxy.get("host_bind"), "127.0.0.1")
    if host == "0.0.0.0":
        host = "127.0.0.1"

    host_port = controller_proxy.get("host_port")
    if host_port is None:
        raise ValueError(f"runtime info file {info_path} has no controller_proxy.host_port")

    port = validate_port(str(host_port))
    return f"http://{host}:{port}"


def build_tunnel_url(proxy_url: str, name: str, port: str) -> str:
    base_url = proxy_url.rstrip("/")

    encoded_name = urllib.parse.quote(name, safe="")
    encoded_port = urllib.parse.quote(port, safe="")

    return f"{base_url}/_tunnel/{encoded_name}/{encoded_port}"


def post_tunnel_request(
    tunnel_url: str,
    username: str,
    password: str,
) -> bytes:
    payload = json.dumps(
        {
            "username": username,
            "password": password,
        }
    ).encode("utf-8")

    request = urllib.request.Request(
        tunnel_url,
        data=payload,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "*/*",
        },
    )

    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read()


def resolve_options(args: argparse.Namespace) -> TunnelOptions:
    legacy = list(args.legacy or [])
    if legacy:
        if len(legacy) != 5:
            raise ValueError(
                "legacy positional usage requires exactly 5 arguments: "
                "<proxy_url> <name> <port> <username> <password>"
            )
        proxy_url, name, port, username, password = legacy
        return TunnelOptions(
            proxy_url=proxy_url,
            name=name,
            port=validate_port(port),
            username=username,
            password=password,
        )

    config_path = Path(args.config).expanduser().resolve()
    config = load_config(config_path)
    defaults = config_defaults(config)

    lab_name = str_config(args.lab_name, defaults["lab_name"])
    name = str_config(args.container, defaults["container"])
    port = validate_port(str_config(args.port, defaults["port"]))
    username = str_config(args.username, defaults["username"])
    password = str_config(args.password, defaults["password"])
    info_path = runtime_info_path(lab_name)
    proxy_url = str_config(args.proxy_url, "") or proxy_url_from_info(info_path)

    return TunnelOptions(
        proxy_url=proxy_url,
        name=name,
        port=port,
        username=username,
        password=password,
        lab_name=lab_name,
        info_path=info_path,
    )


def main() -> int:
    args = parse_args()

    try:
        options = resolve_options(args)
        tunnel_url = build_tunnel_url(options.proxy_url, options.name, options.port)
        response_body = post_tunnel_request(
            tunnel_url=tunnel_url,
            username=options.username,
            password=options.password,
        )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        print(
            f"error: tunnel request failed with HTTP {exc.code} {exc.reason}",
            file=sys.stderr,
        )
        if body:
            print(body, file=sys.stderr)
        return 1
    except urllib.error.URLError as exc:
        print(f"error: failed to reach proxy: {exc.reason}", file=sys.stderr)
        return 1
    except TimeoutError:
        print("error: tunnel request timed out", file=sys.stderr)
        return 1

    if options.lab_name:
        print(f"lab: {options.lab_name}")
    print(f"target: {options.name}:{options.port}")
    print(f"proxy: {options.proxy_url}/")

    if response_body:
        sys.stdout.buffer.write(response_body)
        if not response_body.endswith(b"\n"):
            sys.stdout.write("\n")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())