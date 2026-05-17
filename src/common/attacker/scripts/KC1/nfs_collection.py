#!/usr/bin/env python3
"""Collect free5GC SBI flow evidence from NRF, UDR, PCF, and CHF.

The collector is intentionally permissive: every response is saved, including
4xx/5xx replies, because those replies are useful when checking whether a flow
is wired correctly in the lab.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import socket
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


DEFAULT_TARGET_NF_TYPES = "NRF,AMF,SMF,UDM,UDR,PCF,CHF,AUSF,NSSF,NEF,NWDAF"
DEFAULT_SUPIS = "imsi-208930000000001,imsi-208930000000004"
DEFAULT_SERVING_PLMN = "20893"
DEFAULT_DNN = "internet"
DEFAULT_SST = 1
DEFAULT_SD = "010203"

DEFAULT_NRF_HOST = "nrf-nnrf"
DEFAULT_UDR_HOST = "free5gc-helm-free5gc-udr-service"
DEFAULT_PCF_HOST = "free5gc-helm-free5gc-pcf-service"
DEFAULT_CHF_HOST = "free5gc-helm-free5gc-chf-service"

SERVICE_BY_NF = {
    "UDR": ["nudr-dr"],
    "PCF": [
        "npcf-am-policy-control",
        "npcf-smpolicycontrol",
        "npcf-bdtpolicycontrol",
        "npcf-policyauthorization",
        "npcf-eventexposure",
        "npcf-ue-policy-control",
    ],
    "CHF": ["nchf-convergedcharging"],
}


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def env(name: str, default: str) -> str:
    value = os.getenv(name)
    return default if value is None or value == "" else value


def env_int(name: str, default: int) -> int:
    raw = env(name, str(default))
    try:
        return int(raw)
    except ValueError as exc:
        raise SystemExit(f"{name} must be an integer, got {raw!r}") from exc


def env_list(name: str, default: str) -> list[str]:
    raw = env(name, default)
    return [item.strip() for item in raw.split(",") if item.strip()]


def compact_json(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"))


def safe_name(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"[^a-z0-9_.-]+", "_", value)
    return value.strip("._-") or "request"


def unverified_context(url: str) -> ssl.SSLContext | None:
    if not url.startswith("https://"):
        return None
    if env("VERIFY_TLS", "false").lower() in {"1", "true", "yes", "on"}:
        return ssl.create_default_context()
    return ssl._create_unverified_context()  # noqa: SLF001 - lab self-signed certs.


def base_url(prefix: str, default_host: str, default_port: str = "8080") -> str:
    explicit = os.getenv(f"{prefix}_BASE_URL", "").strip().rstrip("/")
    if explicit:
        return explicit

    scheme = env(f"{prefix}_SCHEME", env("SBI_SCHEME", env("NRF_SCHEME", "https"))).strip() or "https"
    host = env(f"{prefix}_HOST", default_host).strip() or default_host
    port = env(f"{prefix}_PORT", default_port).strip() or default_port
    return f"{scheme}://{host}:{port}"


def join_url(root: str, path: str, query: dict[str, Any] | None = None) -> str:
    url = f"{root.rstrip('/')}/{path.lstrip('/')}"
    if query:
        url = f"{url}?{urllib.parse.urlencode(query, doseq=True)}"
    return url


def absolute_url(root: str, location: str) -> str:
    if location.startswith(("http://", "https://")):
        return location
    return join_url(root, location)


class Collector:
    def __init__(self) -> None:
        self.out_dir = Path(env("UDM_EX_OUT_DIR", f"{env('DATA_PATH', '/tmp/KCData')}/KC1/nfs-collection"))
        self.timeout = env_int("SBI_TIMEOUT_SECONDS", env_int("NRF_TIMEOUT_SECONDS", 10))
        self.counter = 0
        self.calls: list[dict[str, Any]] = []
        self.discovery: dict[str, dict[str, str]] = {}
        self.nf_api_roots: dict[str, str] = {}
        self.started_at = utc_now()
        self.out_dir.mkdir(parents=True, exist_ok=True)

    def write_json(self, relative: str, payload: dict[str, Any]) -> None:
        path = self.out_dir / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    def request(
        self,
        label: str,
        method: str,
        url: str,
        *,
        section: str,
        requester_nf_type: str,
        body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        self.counter += 1
        data = None
        request_headers = {
            "Accept": "application/json",
            "User-Agent": "honeypot-attacker-kc1-collector/1.0",
            "X-Requester-NF-Type": requester_nf_type,
            "3gpp-Sbi-Requester-Nf-Type": requester_nf_type,
        }
        if body is not None:
            data = compact_json(body).encode("utf-8")
            request_headers["Content-Type"] = "application/json"
        if headers:
            request_headers.update(headers)

        started = time.monotonic()
        status: int | None = None
        response_headers: dict[str, str] = {}
        body_text = ""
        error = ""

        request = urllib.request.Request(url, data=data, method=method, headers=request_headers)
        try:
            with urllib.request.urlopen(request, context=unverified_context(url), timeout=self.timeout) as response:
                status = response.status
                response_headers = dict(response.headers.items())
                body_text = response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            status = exc.code
            response_headers = dict(exc.headers.items())
            body_text = exc.read().decode("utf-8", errors="replace")
        except Exception as exc:  # noqa: BLE001 - errors are saved as evidence.
            error = repr(exc)

        elapsed_ms = int((time.monotonic() - started) * 1000)
        parsed_body: Any | None = None
        if body_text:
            try:
                parsed_body = json.loads(body_text)
            except json.JSONDecodeError:
                parsed_body = None

        relative = f"{safe_name(section)}/{self.counter:03d}_{safe_name(label)}.json"
        record = {
            "label": label,
            "section": section,
            "requesterNfType": requester_nf_type,
            "request": {
                "method": method,
                "url": url,
                "headers": request_headers,
                "json": body,
            },
            "response": {
                "status": status,
                "headers": response_headers,
                "elapsedMs": elapsed_ms,
                "bodyText": body_text,
                "bodyJson": parsed_body,
                "error": error,
            },
        }
        self.write_json(relative, record)

        manifest_item = {
            "label": label,
            "section": section,
            "requesterNfType": requester_nf_type,
            "method": method,
            "url": url,
            "status": status,
            "error": error,
            "file": relative,
        }
        self.calls.append(manifest_item)
        return record

    def synthetic_result(self, label: str, section: str, payload: dict[str, Any]) -> None:
        self.counter += 1
        relative = f"{safe_name(section)}/{self.counter:03d}_{safe_name(label)}.json"
        self.write_json(relative, payload)
        self.calls.append(
            {
                "label": label,
                "section": section,
                "requesterNfType": payload.get("nfType", ""),
                "method": "LOCAL",
                "url": "",
                "status": payload.get("status"),
                "error": payload.get("error", ""),
                "file": relative,
            }
        )

    def remember_nf_profiles(self, payload: Any) -> None:
        if isinstance(payload, dict):
            candidates = payload.get("nfInstances") or payload.get("nfProfiles") or payload.get("items") or []
            if "nfInstanceId" in payload or "nfType" in payload:
                candidates = [payload]
        elif isinstance(payload, list):
            candidates = payload
        else:
            candidates = []

        for profile in candidates:
            if not isinstance(profile, dict):
                continue
            nf_type = str(profile.get("nfType", "")).upper()
            api_root = str(profile.get("apiPrefix") or profile.get("apiRoot") or "").rstrip("/")
            if nf_type and api_root:
                self.nf_api_roots.setdefault(nf_type, api_root)
            for service in profile.get("nfServices") or []:
                if not isinstance(service, dict):
                    continue
                service_name = str(service.get("serviceName", "")).strip()
                service_root = str(service.get("apiPrefix") or service.get("apiRoot") or api_root).rstrip("/")
                if nf_type and service_name and service_root:
                    self.discovery.setdefault(nf_type, {})[service_name] = service_root

    def service_root(self, nf_type: str, service_name: str, fallback: str) -> str:
        return (
            self.discovery.get(nf_type, {}).get(service_name)
            or self.nf_api_roots.get(nf_type)
            or fallback.rstrip("/")
        )

    def finalize(self) -> None:
        ok = sum(1 for item in self.calls if isinstance(item.get("status"), int) and 200 <= int(item["status"]) < 300)
        manifest = {
            "startedAt": self.started_at,
            "finishedAt": utc_now(),
            "outputDir": str(self.out_dir),
            "totals": {
                "calls": len(self.calls),
                "http2xx": ok,
                "withErrors": sum(1 for item in self.calls if item.get("error")),
            },
            "discoveredApiRoots": self.nf_api_roots,
            "discoveredServices": self.discovery,
            "calls": self.calls,
        }
        self.write_json("manifest.json", manifest)
        summary_lines = [
            "KC1 UDM/UDR/PCF/CHF collector",
            f"started: {manifest['startedAt']}",
            f"finished: {manifest['finishedAt']}",
            f"output: {self.out_dir}",
            f"calls: {len(self.calls)}",
            f"http2xx: {ok}",
            f"errors: {manifest['totals']['withErrors']}",
            "",
            "See manifest.json and per-request JSON files for full request/response evidence.",
        ]
        (self.out_dir / "summary.txt").write_text("\n".join(summary_lines) + "\n", encoding="utf-8")


def add_attacker_lib_path() -> None:
    candidates = [Path(os.getenv("ATTACKER_LIB_DIR", "/opt/attacker-lib"))]
    here = Path(__file__).resolve()
    candidates.append(here.parents[2] / "lib")
    for candidate in candidates:
        if candidate.exists():
            sys.path.insert(0, str(candidate))
            return


def register_requester_profiles(collector: Collector) -> None:
    add_attacker_lib_path()
    try:
        from nwdaf import nrf_register
    except Exception as exc:  # noqa: BLE001 - registration is helpful but non-fatal.
        collector.synthetic_result(
            "register_requester_profiles_unavailable",
            "registration",
            {"status": None, "error": repr(exc), "message": "could not import nwdaf.nrf_register"},
        )
        return

    for nf_type in env_list("UDM_EX_REGISTER_NF_TYPES", "NWDAF,UDM,PCF,AMF,SMF"):
        try:
            status, body = nrf_register.register_profile(nf_type)
            collector.synthetic_result(
                f"register_as_{nf_type.lower()}",
                "registration",
                {"nfType": nf_type, "status": status, "bodyText": body},
            )
        except Exception as exc:  # noqa: BLE001
            collector.synthetic_result(
                f"register_as_{nf_type.lower()}",
                "registration",
                {"nfType": nf_type, "status": None, "error": repr(exc)},
            )


def collect_nrf(collector: Collector, nrf_root: str) -> None:
    response = collector.request(
        "nrf_nf_registrations_all",
        "GET",
        join_url(nrf_root, "/nnrf-nfm/v1/nf-instances"),
        section="nrf",
        requester_nf_type="NWDAF",
    )
    collector.remember_nf_profiles(response["response"]["bodyJson"])

    for target_nf_type in env_list("UDM_EX_DISCOVERY_TARGETS", DEFAULT_TARGET_NF_TYPES):
        response = collector.request(
            f"nrf_discovery_{target_nf_type.lower()}",
            "GET",
            join_url(
                nrf_root,
                "/nnrf-disc/v1/nf-instances",
                {"target-nf-type": target_nf_type, "requester-nf-type": "NWDAF"},
            ),
            section="nrf",
            requester_nf_type="NWDAF",
        )
        collector.remember_nf_profiles(response["response"]["bodyJson"])

    for nf_type, service_names in SERVICE_BY_NF.items():
        for service_name in service_names:
            response = collector.request(
                f"nrf_discovery_{nf_type.lower()}_{service_name}",
                "GET",
                join_url(
                    nrf_root,
                    "/nnrf-disc/v1/nf-instances",
                    {
                        "target-nf-type": nf_type,
                        "requester-nf-type": "NWDAF",
                        "service-names": service_name,
                    },
                ),
                section="nrf",
                requester_nf_type="NWDAF",
            )
            collector.remember_nf_profiles(response["response"]["bodyJson"])


def udr_subscriber_paths(supi: str, serving_plmn: str, snssai: dict[str, Any], dnn: str) -> list[tuple[str, str, dict[str, Any] | None]]:
    snssai_query = compact_json(snssai)
    return [
        ("auth_subscription", f"/nudr-dr/v1/subscription-data/{supi}/authentication-data/authentication-subscription", None),
        ("auth_status", f"/nudr-dr/v1/subscription-data/{supi}/authentication-data/authentication-status", None),
        ("am_data", f"/nudr-dr/v1/subscription-data/{supi}/{serving_plmn}/provisioned-data/am-data", None),
        (
            "smf_selection",
            f"/nudr-dr/v1/subscription-data/{supi}/{serving_plmn}/provisioned-data/smf-selection-subscription-data",
            None,
        ),
        ("sm_data_all", f"/nudr-dr/v1/subscription-data/{supi}/{serving_plmn}/provisioned-data/sm-data", None),
        (
            "sm_data_slice_dnn",
            f"/nudr-dr/v1/subscription-data/{supi}/{serving_plmn}/provisioned-data/sm-data",
            {"single-nssai": snssai_query, "dnn": dnn},
        ),
        ("trace_data", f"/nudr-dr/v1/subscription-data/{supi}/{serving_plmn}/provisioned-data/trace-data", None),
        ("amf_context_3gpp", f"/nudr-dr/v1/subscription-data/{supi}/context-data/amf-3gpp-access", None),
        ("smf_registrations", f"/nudr-dr/v1/subscription-data/{supi}/context-data/smf-registrations", None),
        ("operator_specific", f"/nudr-dr/v1/subscription-data/{supi}/operator-specific-data", None),
    ]


def udr_policy_paths(supi: str, snssai: dict[str, Any], dnn: str) -> list[tuple[str, str, dict[str, Any] | None]]:
    snssai_query = compact_json(snssai)
    return [
        ("policy_am_data", f"/nudr-dr/v1/policy-data/ues/{supi}/am-data", None),
        ("policy_sm_data_all", f"/nudr-dr/v1/policy-data/ues/{supi}/sm-data", None),
        ("policy_sm_data_slice_dnn", f"/nudr-dr/v1/policy-data/ues/{supi}/sm-data", {"snssai": snssai_query, "dnn": dnn}),
        ("ue_policy_set", f"/nudr-dr/v1/policy-data/ues/{supi}/ue-policy-set", None),
        ("policy_operator_specific", f"/nudr-dr/v1/policy-data/ues/{supi}/operator-specific-data", None),
    ]


def collect_udr(collector: Collector, fallback_root: str) -> None:
    root = collector.service_root("UDR", "nudr-dr", fallback_root)
    supis = env_list("UDM_EX_SUPIS", DEFAULT_SUPIS)
    serving_plmn = env("UDM_EX_SERVING_PLMN", DEFAULT_SERVING_PLMN)
    snssai = {"sst": env_int("UDM_EX_SST", DEFAULT_SST), "sd": env("UDM_EX_SD", DEFAULT_SD)}
    dnn = env("UDM_EX_DNN", DEFAULT_DNN)

    for supi in supis:
        for label, path, query in udr_subscriber_paths(supi, serving_plmn, snssai, dnn):
            collector.request(
                f"udr_as_udm_{supi}_{label}",
                "GET",
                join_url(root, path, query),
                section="udr_subscriber_data",
                requester_nf_type="UDM",
            )
        for label, path, query in udr_policy_paths(supi, snssai, dnn):
            collector.request(
                f"udr_as_pcf_{supi}_{label}",
                "GET",
                join_url(root, path, query),
                section="udr_policy_data",
                requester_nf_type="PCF",
            )


def pcf_am_policy_body(supi: str, notification_uri: str) -> dict[str, Any]:
    return {
        "supi": supi,
        "accessType": "3GPP_ACCESS",
        "notificationUri": f"{notification_uri.rstrip('/')}/am-policy",
        "servingPlmn": {"mcc": env("UDM_EX_MCC", "208"), "mnc": env("UDM_EX_MNC", "93")},
        "pei": env("UDM_EX_PEI", "imei-356938035643809"),
        "ratType": "NR",
    }


def pcf_sm_policy_body(supi: str, notification_uri: str, snssai: dict[str, Any], dnn: str) -> dict[str, Any]:
    return {
        "supi": supi,
        "pduSessionId": env_int("UDM_EX_PDU_SESSION_ID", 10),
        "pduSessionType": "IPV4",
        "dnn": dnn,
        "sliceInfo": snssai,
        "notificationUri": f"{notification_uri.rstrip('/')}/sm-policy",
        "accessType": "3GPP_ACCESS",
        "ratType": "NR",
        "servingNetwork": {"mcc": env("UDM_EX_MCC", "208"), "mnc": env("UDM_EX_MNC", "93")},
        "ueIpv4": env("UDM_EX_UE_IPV4", "10.60.0.1"),
    }


def follow_location(collector: Collector, label: str, response: dict[str, Any], requester_nf_type: str, root: str) -> None:
    location = response["response"]["headers"].get("Location") or response["response"]["headers"].get("location")
    if not location:
        body = response["response"].get("bodyJson")
        if isinstance(body, dict):
            location = body.get("resourceUri") or body.get("self") or body.get("href")
    if location:
        collector.request(
            label,
            "GET",
            absolute_url(root, str(location)),
            section="pcf_policy_decision",
            requester_nf_type=requester_nf_type,
        )


def collect_pcf(collector: Collector, fallback_root: str) -> None:
    notification_uri = env("UDM_EX_NOTIFICATION_URI", "http://nwdaf-nnwdaf:8080/kc1")
    supis = env_list("UDM_EX_SUPIS", DEFAULT_SUPIS)
    snssai = {"sst": env_int("UDM_EX_SST", DEFAULT_SST), "sd": env("UDM_EX_SD", DEFAULT_SD)}
    dnn = env("UDM_EX_DNN", DEFAULT_DNN)
    am_root = collector.service_root("PCF", "npcf-am-policy-control", fallback_root)
    sm_root = collector.service_root("PCF", "npcf-smpolicycontrol", fallback_root)

    for supi in supis:
        am_response = collector.request(
            f"pcf_as_amf_{supi}_am_policy_create",
            "POST",
            join_url(am_root, "/npcf-am-policy-control/v1/policies"),
            section="pcf_policy_decision",
            requester_nf_type="AMF",
            body=pcf_am_policy_body(supi, notification_uri),
        )
        follow_location(collector, f"pcf_as_amf_{supi}_am_policy_get", am_response, "AMF", am_root)

        sm_response = collector.request(
            f"pcf_as_smf_{supi}_sm_policy_create",
            "POST",
            join_url(sm_root, "/npcf-smpolicycontrol/v1/sm-policies"),
            section="pcf_policy_decision",
            requester_nf_type="SMF",
            body=pcf_sm_policy_body(supi, notification_uri, snssai, dnn),
        )
        follow_location(collector, f"pcf_as_smf_{supi}_sm_policy_get", sm_response, "SMF", sm_root)


def chf_initial_body(supi: str, snssai: dict[str, Any], dnn: str) -> dict[str, Any]:
    sequence = env_int("UDM_EX_CHF_SEQUENCE", 1)
    return {
        "subscriberIdentifier": supi,
        "nfConsumerIdentification": {
            "nFName": env("UDM_EX_SMF_NAME", socket.gethostname()),
            "nodeFunctionality": "SMF",
            "nFIPv4Address": env("POD_IP", "127.0.0.1"),
        },
        "invocationTimeStamp": utc_now(),
        "invocationSequenceNumber": sequence,
        "oneTimeEvent": False,
        "pDUSessionChargingInformation": {
            "chargingId": env_int("UDM_EX_CHARGING_ID", 1),
            "pduSessionInformation": {
                "pduSessionID": env_int("UDM_EX_PDU_SESSION_ID", 10),
                "pduType": "IPV4",
                "dnnId": dnn,
                "networkSlicingInfo": {"sNSSAI": snssai},
            },
            "userInformation": {
                "servedGPSI": env("UDM_EX_GPSI", "msisdn-0900000000"),
                "servedPEI": env("UDM_EX_PEI", "imei-356938035643809"),
            },
        },
    }


def chf_update_body(sequence: int) -> dict[str, Any]:
    return {
        "invocationTimeStamp": utc_now(),
        "invocationSequenceNumber": sequence,
        "triggers": [{"triggerType": "QUOTA_MANAGEMENT"}],
        "pDUSessionChargingInformation": {
            "chargingId": env_int("UDM_EX_CHARGING_ID", 1),
            "timeUsage": env_int("UDM_EX_CHF_TIME_USAGE", 1),
            "totalVolume": env_int("UDM_EX_CHF_TOTAL_VOLUME", 1024),
        },
    }


def chf_release_body(sequence: int) -> dict[str, Any]:
    return {
        "invocationTimeStamp": utc_now(),
        "invocationSequenceNumber": sequence,
        "pDUSessionChargingInformation": {
            "chargingId": env_int("UDM_EX_CHARGING_ID", 1),
            "timeUsage": env_int("UDM_EX_CHF_TIME_USAGE", 1),
            "totalVolume": env_int("UDM_EX_CHF_TOTAL_VOLUME", 1024),
        },
    }


def charging_refs(response: dict[str, Any]) -> list[str]:
    refs: list[str] = []
    headers = response["response"]["headers"]
    for key in ("Location", "location"):
        if headers.get(key):
            refs.append(str(headers[key]).rstrip("/"))
    body = response["response"].get("bodyJson")
    if isinstance(body, dict):
        for key in ("chargingDataRef", "resourceUri", "self", "href"):
            if body.get(key):
                refs.append(str(body[key]).rstrip("/"))
    return list(dict.fromkeys(refs))


def collect_chf(collector: Collector, fallback_root: str) -> None:
    root = collector.service_root("CHF", "nchf-convergedcharging", fallback_root)
    supis = env_list("UDM_EX_SUPIS", DEFAULT_SUPIS)
    snssai = {"sst": env_int("UDM_EX_SST", DEFAULT_SST), "sd": env("UDM_EX_SD", DEFAULT_SD)}
    dnn = env("UDM_EX_DNN", DEFAULT_DNN)
    versions = env_list("UDM_EX_CHF_VERSIONS", "v3,v2,v1")

    for supi in supis:
        for version in versions:
            create = collector.request(
                f"chf_as_smf_{supi}_{version}_charging_create",
                "POST",
                join_url(root, f"/nchf-convergedcharging/{version}/chargingdata"),
                section="chf_charging_quota_reporting",
                requester_nf_type="SMF",
                body=chf_initial_body(supi, snssai, dnn),
            )
            if not (isinstance(create["response"]["status"], int) and 200 <= create["response"]["status"] < 300):
                continue

            for ref in charging_refs(create):
                ref_url = absolute_url(root, ref)
                collector.request(
                    f"chf_as_smf_{supi}_{version}_quota_update",
                    "POST",
                    join_url(ref_url, "update"),
                    section="chf_charging_quota_reporting",
                    requester_nf_type="SMF",
                    body=chf_update_body(2),
                )
                collector.request(
                    f"chf_as_smf_{supi}_{version}_reporting_release",
                    "POST",
                    join_url(ref_url, "release"),
                    section="chf_charging_quota_reporting",
                    requester_nf_type="SMF",
                    body=chf_release_body(3),
                )
            break


def main() -> int:
    collector = Collector()
    nrf_root = base_url("NRF", DEFAULT_NRF_HOST, "8000")
    udr_root = base_url("UDR", DEFAULT_UDR_HOST)
    pcf_root = base_url("PCF", DEFAULT_PCF_HOST)
    chf_root = base_url("CHF", DEFAULT_CHF_HOST)

    try:
        register_requester_profiles(collector)
        collect_nrf(collector, nrf_root)
        collect_udr(collector, udr_root)
        collect_pcf(collector, pcf_root)
        collect_chf(collector, chf_root)
    finally:
        collector.finalize()

    print(f"KC1 collector output written to {collector.out_dir}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
