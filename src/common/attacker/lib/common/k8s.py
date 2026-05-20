#!/usr/bin/env python3
"""Small Kubernetes API helpers for attacker scripts."""

from __future__ import annotations

import json
import os
import ssl
import urllib.request
from pathlib import Path
from typing import Any


def service_api_server() -> str:
    """Return the in-cluster Kubernetes API server URL."""
    host = os.environ.get("KUBERNETES_SERVICE_HOST", "kubernetes.default.svc")
    port = os.environ.get("KUBERNETES_SERVICE_PORT", "443")
    return f"https://{host}:{port}"


def service_account_token(path: str = "/var/run/secrets/kubernetes.io/serviceaccount/token") -> str:
    """Read the mounted ServiceAccount token."""
    return Path(path).read_text(encoding="utf-8").strip()


def request_json(method: str, url: str, *, token: str = "", payload: Any | None = None, content_type: str = "application/json") -> Any:
    """Issue an insecure Kubernetes API request and parse JSON responses."""
    data = None
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if payload is not None:
        headers["Content-Type"] = content_type
        data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method=method.upper())
    context = ssl._create_unverified_context()
    with urllib.request.urlopen(req, timeout=30, context=context) as response:
        body = response.read()
    return json.loads(body.decode("utf-8")) if body else {}
