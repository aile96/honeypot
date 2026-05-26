from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any


HOOK = Path("src/opentelemetry/hooks/pipeline/HOOK_POST_03.py").resolve()


def load_hook(controller_importer):
    hook = controller_importer.script("otel_hook_post_03", HOOK)
    hook.CONFIG = {"CP_NETWORK": "lab"}
    hook.STATE = {"values": {}}
    return hook


def network_inspect(subnet: str, *, gateway: str = "172.18.0.1", used: list[str] | None = None) -> list[dict[str, Any]]:
    containers = {
        f"container-{index}": {"IPv4Address": address}
        for index, address in enumerate(used or [], start=1)
    }
    return [
        {
            "Name": "lab",
            "IPAM": {
                "Config": [
                    {
                        "Subnet": subnet,
                        "Gateway": gateway,
                    }
                ]
            },
            "Containers": containers,
        }
    ]


def stub_network_inspect(hook, data: list[dict[str, Any]]) -> None:
    def fake_run_cmd(cmd, **kwargs):
        assert cmd == ["docker", "network", "inspect", "lab"]
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(data), stderr="")

    hook.run_cmd = fake_run_cmd


def test_configure_metallb_addresses_prefers_200_201_when_free(controller_importer, monkeypatch) -> None:
    hook = load_hook(controller_importer)
    logs: list[str] = []
    monkeypatch.setattr(hook, "log", logs.append)
    stub_network_inspect(hook, network_inspect("172.18.0.0/16", used=["172.18.0.2/16"]))

    subnet, frontend_proxy_ip, generic_svc_addr = hook.configure_metallb_addresses()

    assert subnet == "172.18.0.0/16"
    assert frontend_proxy_ip == "172.18.0.200"
    assert generic_svc_addr == "172.18.0.201"
    assert hook.CONFIG["FRONTEND_PROXY_IP"] == "172.18.0.200"
    assert hook.CONFIG["GENERIC_SVC_ADDR"] == "172.18.0.201"
    assert hook.STATE["values"]["metallb_ip_selection"]["pool"] == "172.18.0.200-172.18.0.201"
    assert any("Selected MetalLB IPs" in line for line in logs)


def test_configure_metallb_addresses_skips_used_200_201(controller_importer, monkeypatch) -> None:
    hook = load_hook(controller_importer)
    logs: list[str] = []
    monkeypatch.setattr(hook, "log", logs.append)
    stub_network_inspect(
        hook,
        network_inspect(
            "172.18.0.0/16",
            used=[
                "172.18.0.2/16",
                "172.18.0.200/16",
                "172.18.0.201/16",
            ],
        ),
    )

    _, frontend_proxy_ip, generic_svc_addr = hook.configure_metallb_addresses()

    assert frontend_proxy_ip == "172.18.0.202"
    assert generic_svc_addr == "172.18.0.203"
    assert hook.STATE["values"]["FRONTEND_PROXY_IP"] == "172.18.0.202"
    assert hook.STATE["values"]["GENERIC_SVC_ADDR"] == "172.18.0.203"
    assert any("frontend-proxy=172.18.0.202, generic=172.18.0.203" in line for line in logs)
