#!/usr/bin/env python3
"""Register a lightweight test NF profile with the free5GC NRF.

The container uses this module both for its normal NWDAF self-registration and
for the optional registration-sequence test. Profiles are intentionally minimal:
they contain enough NFManagement metadata for NRF acceptance without pretending
to implement real SMF, AMF, UDM, or PCF behavior.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import ssl
import sys
import time
import urllib.error
import urllib.request
import uuid
from typing import Any

DEFAULT_PLMN = {"mcc": "208", "mnc": "93"}
DEFAULT_SNSSAI = {"sst": 1, "sd": "010203"}
SERVICE_BY_NF_TYPE = {
    "NWDAF": "nnwdaf-analyticsinfo",
    "SMF": "nsmf-pdusession",
    "AMF": "namf-comm",
    "UDM": "nudm-sdm",
    "PCF": "npcf-smpolicycontrol",
}
NRF_MANAGEMENT_SCOPE = "nnrf-nfm"


def env_bool(name: str, default: bool = False) -> bool:
    """Return a boolean environment value."""
    raw = os.getenv(name, "true" if default else "false").strip().lower()
    return raw in {"1", "true", "yes", "y", "on", "enabled"}


def env_int(name: str, default: int) -> int:
    """Return an integer environment value."""
    raw = os.getenv(name, str(default)).strip()
    try:
        return int(raw)
    except ValueError as exc:
        raise SystemExit(f"{name} must be an integer, got {raw!r}") from exc


def pod_ip() -> str:
    """Return the pod IP passed by Kubernetes, or a best-effort local IP."""
    explicit = os.getenv("POD_IP", "").strip()
    if explicit:
        return explicit

    try:
        return socket.gethostbyname(socket.gethostname())
    except OSError:
        return "127.0.0.1"


def service_host() -> str:
    """Return the DNS name exposed in the registered NF profile."""
    return os.getenv("NWDAF_SERVICE_HOST", os.getenv("HOSTNAME", "nwdaf-nnwdaf")).strip()


def service_port() -> int:
    """Return the port exposed in the registered NF profile."""
    return env_int("NWDAF_SERVICE_PORT", env_int("NWDAF_PORT", 8080))


def nrf_base_url() -> str:
    """Build the primary NRF base URL from environment variables."""
    explicit = os.getenv("NRF_BASE_URL", "").strip().rstrip("/")
    if explicit:
        return explicit

    scheme = os.getenv("NRF_SCHEME", "https").strip() or "https"
    host = os.getenv("NRF_HOST", "nrf-nnrf").strip() or "nrf-nnrf"
    port = os.getenv("NRF_PORT", "8000").strip() or "8000"
    return f"{scheme}://{host}:{port}"


def nrf_base_urls() -> list[str]:
    """Return all NRF base URLs that should receive registration updates."""
    explicit = os.getenv("NRF_BASE_URLS", "").strip()
    if explicit:
        return [item.strip().rstrip("/") for item in explicit.split(",") if item.strip()]

    return [nrf_base_url()]


def deterministic_instance_id(nf_type: str) -> str:
    """Return a stable UUID so repeated tests update instead of duplicating profiles."""
    seed = os.getenv("NF_INSTANCE_SEED", socket.gethostname())
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, f"free5gc-test-nwdaf:{seed}:{nf_type.upper()}"))


def nf_service(service_name: str, nf_type: str) -> dict[str, Any]:
    """Build the service block inserted into the NF profile."""
    host = service_host()
    port = service_port()
    scheme = os.getenv("NWDAF_SERVICE_SCHEME", "http").strip() or "http"

    return {
        "serviceInstanceId": f"test-nwdaf-{nf_type.lower()}-{service_name}",
        "serviceName": service_name,
        "versions": [
            {
                "apiVersionInUri": "v1",
                "apiFullVersion": "1.0.0",
            }
        ],
        "scheme": scheme,
        "nfServiceStatus": "REGISTERED",
        "fqdn": host,
        "ipEndPoints": [
            {
                "ipv4Address": pod_ip(),
                "transport": "TCP",
                "port": port,
            }
        ],
        "apiPrefix": f"{scheme}://{host}:{port}",
    }


def build_profile(nf_type: str, instance_id: str | None = None) -> dict[str, Any]:
    """Build a minimal NRF NFProfile for a given NF type."""
    nf_type = nf_type.upper()
    service_name = os.getenv("NF_SERVICE_NAME", SERVICE_BY_NF_TYPE.get(nf_type, "nnwdaf-analyticsinfo"))
    instance_id = instance_id or os.getenv("NF_INSTANCE_ID", "").strip() or deterministic_instance_id(nf_type)

    return {
        "nfInstanceId": instance_id,
        "nfType": nf_type,
        "nfStatus": "REGISTERED",
        "heartBeatTimer": env_int("HEARTBEAT_TIMER", 60),
        "fqdn": service_host(),
        "ipv4Addresses": [pod_ip()],
        "plmnList": [DEFAULT_PLMN],
        "sNssais": [DEFAULT_SNSSAI],
        "nfServices": [nf_service(service_name, nf_type)],
    }


def request_context(base_url: str | None = None) -> ssl.SSLContext | None:
    """Return a TLS context for NRF calls.

    The lab uses self-signed NRF certificates. By default the test client keeps
    TLS enabled but disables verification so the NF can register without adding
    another certificate distribution path.
    """
    if not (base_url or nrf_base_url()).startswith("https://"):
        return None

    if env_bool("VERIFY_TLS", False):
        return ssl.create_default_context()

    return ssl._create_unverified_context()  # noqa: SLF001 - required for lab self-signed NRF certs.


def register_profile(nf_type: str, instance_id: str | None = None) -> tuple[int, str]:
    """PUT an NF profile to the NRF and return status/body."""
    profile = build_profile(nf_type, instance_id=instance_id)
    url = f"{nrf_base_url()}/nnrf-nfm/v1/nf-instances/{profile['nfInstanceId']}"
    body = json.dumps(profile, separators=(",", ":")).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        method="PUT",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )

    try:
        with urllib.request.urlopen(
            request,
            context=request_context(nrf_base_url()),
            timeout=env_int("NRF_TIMEOUT_SECONDS", 10),
        ) as response:
            return response.status, response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", errors="replace")
    except urllib.error.URLError as exc:
        raise RuntimeError(f"NRF registration failed for {nf_type}: {exc}") from exc


def oauth_token(
    nf_type: str,
    *,
    instance_id: str | None = None,
    base_url: str | None = None,
    target_nf_type: str = "NRF",
    scope: str = NRF_MANAGEMENT_SCOPE,
) -> tuple[int, str, str]:
    """Request a free5GC NRF OAuth access token.

    free5GC expects service consumers to ask the NRF for a client-credentials
    token before calling protected SBI APIs such as Nnrf_NFManagement DELETE.
    """
    nf_type = nf_type.upper()
    instance_id = instance_id or os.getenv("NF_INSTANCE_ID", "").strip() or deterministic_instance_id(nf_type)
    base_url = (base_url or nrf_base_url()).rstrip("/")
    url = f"{base_url}/oauth2/token"
    payload = {
        "grant_type": "client_credentials",
        "nfInstanceId": instance_id,
        "nfType": nf_type,
        "targetNfType": target_nf_type.upper(),
        "scope": scope,
    }
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )

    try:
        with urllib.request.urlopen(
            request,
            context=request_context(base_url),
            timeout=env_int("NRF_TIMEOUT_SECONDS", 10),
        ) as response:
            response_body = response.read().decode("utf-8", errors="replace")
            token = parse_access_token(response_body)
            return response.status, response_body, token
    except urllib.error.HTTPError as exc:
        response_body = exc.read().decode("utf-8", errors="replace")
        return exc.code, response_body, parse_access_token(response_body)
    except urllib.error.URLError as exc:
        raise RuntimeError(f"NRF OAuth token request failed for {nf_type} at {base_url}: {exc}") from exc


def parse_access_token(response_body: str) -> str:
    """Extract an OAuth access token from known free5GC/3GPP response fields."""
    try:
        payload = json.loads(response_body)
    except json.JSONDecodeError:
        return ""
    for key in ("access_token", "accessToken", "AccessToken"):
        token = payload.get(key)
        if isinstance(token, str) and token:
            return token
    return ""


def authorization_headers(
    nf_type: str,
    *,
    instance_id: str,
    base_url: str,
    scope: str = NRF_MANAGEMENT_SCOPE,
) -> dict[str, str]:
    """Return Authorization headers for NRF management calls when OAuth works."""
    if not env_bool("NRF_OAUTH_ENABLED", True):
        return {}

    status, body, token = oauth_token(nf_type, instance_id=instance_id, base_url=base_url, scope=scope)
    if token:
        print(
            f"oauth-token-from={base_url} nfType={nf_type.upper()} status={status} scope={scope}",
            flush=True,
        )
        return {"Authorization": f"Bearer {token}"}

    if env_bool("NRF_OAUTH_STRICT", False):
        raise RuntimeError(f"NRF OAuth token request failed for {nf_type}: HTTP {status}: {body}")

    print(
        f"oauth-token-unavailable-from={base_url} nfType={nf_type.upper()} status={status} body={body}",
        flush=True,
    )
    return {}


def unregister_profile(nf_type: str, instance_id: str | None = None, base_url: str | None = None) -> tuple[int, str]:
    """DELETE an NF profile from the NRF and return status/body."""
    nf_type = nf_type.upper()
    instance_id = instance_id or os.getenv("NF_INSTANCE_ID", "").strip() or deterministic_instance_id(nf_type)
    base_url = (base_url or nrf_base_url()).rstrip("/")
    url = f"{base_url}/nnrf-nfm/v1/nf-instances/{instance_id}"
    headers = {"Accept": "application/json"}
    headers.update(authorization_headers(nf_type, instance_id=instance_id, base_url=base_url))
    request = urllib.request.Request(url, method="DELETE", headers=headers)

    try:
        with urllib.request.urlopen(
            request,
            context=request_context(base_url),
            timeout=env_int("NRF_TIMEOUT_SECONDS", 10),
        ) as response:
            return response.status, response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", errors="replace")
    except urllib.error.URLError as exc:
        raise RuntimeError(f"NRF deregistration failed for {nf_type} at {base_url}: {exc}") from exc


def wait_and_register(nf_type: str, timeout_seconds: int, retry_seconds: int) -> None:
    """Retry registration until success or timeout."""
    deadline = time.monotonic() + timeout_seconds
    last_error = ""

    while time.monotonic() <= deadline:
        try:
            status, body = register_profile(nf_type)
            if 200 <= status < 300:
                print(f"registered {nf_type} with NRF ({status})", flush=True)
                return
            last_error = f"NRF returned HTTP {status}: {body}"
        except Exception as exc:  # noqa: BLE001 - keep retry loop resilient in the lab.
            last_error = str(exc)

        print(f"waiting for NRF registration of {nf_type}: {last_error}", flush=True)
        time.sleep(retry_seconds)

    raise SystemExit(f"unable to register {nf_type} before timeout: {last_error}")


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description="Register one test NF profile in free5GC NRF")
    parser.add_argument("--nf-type", default=os.getenv("NF_TYPE", "NWDAF"), help="NF type to register")
    parser.add_argument("--instance-id", default=os.getenv("NF_INSTANCE_ID", ""), help="Optional NF instance UUID")
    parser.add_argument("--wait", action="store_true", help="Retry until NRF accepts the profile")
    return parser.parse_args()


def main() -> int:
    """CLI entrypoint for single-profile registration."""
    args = parse_args()
    if args.wait:
        wait_and_register(args.nf_type, env_int("REGISTER_TIMEOUT_SECONDS", 180), env_int("REGISTER_RETRY_SECONDS", 3))
        return 0

    status, body = register_profile(args.nf_type, instance_id=args.instance_id or None)
    print(json.dumps({"nfType": args.nf_type.upper(), "status": status, "body": body}, indent=2), flush=True)
    return 0 if 200 <= status < 300 else 1


if __name__ == "__main__":
    sys.exit(main())
