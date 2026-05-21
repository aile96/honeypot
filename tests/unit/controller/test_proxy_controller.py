from __future__ import annotations

import json
from pathlib import Path

import pytest


def write_proxy_config(tmp_path: Path, state_file: Path, *, proxy_logs: bool | None = None) -> Path:
    config_file = tmp_path / "config.toml"
    text = (
        "[config]\n"
        "LAB_NAME = \"honeypotlab\"\n"
        "HOST_SOCKET = false\n"
        "CONTROLLER_PROXY_CONTAINER_PORT = 18080\n"
        f"STATE_FILE = \"{state_file}\"\n"
    )
    if proxy_logs is not None:
        text += f"CONTROLLER_PROXY_LOGS = {'true' if proxy_logs else 'false'}\n"
    config_file.write_text(text, encoding="utf-8")
    return config_file


@pytest.mark.unit
def test_state_object_and_kube_api_proxy_target(controller_importer, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    state_file = tmp_path / "state.json"
    state_file.write_text(
        json.dumps(
            {
                "values": {
                    "FRONTEND_PROXY_IP": "10.1.2.3",
                    "kube_api_proxy_target": {"enabled": True, "host": "control-plane", "port": 6443},
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("CONFIG_FILE", str(write_proxy_config(tmp_path, state_file)))

    proxy = controller_importer.module("app.start_proxy")

    assert proxy.state_object("FRONTEND_PROXY_IP") == "10.1.2.3"
    assert proxy.kube_api_proxy_target() == ("control-plane", 6443)


@pytest.mark.unit
def test_proxy_logs_are_enabled_by_default(
    controller_importer,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    state_file = tmp_path / "state.json"
    state_file.write_text('{"values":{}}', encoding="utf-8")
    monkeypatch.setenv("CONFIG_FILE", str(write_proxy_config(tmp_path, state_file)))

    proxy = controller_importer.module("app.start_proxy")
    proxy.log("visible")

    captured = capsys.readouterr()
    assert "proxy: visible" in captured.out


@pytest.mark.unit
def test_proxy_logs_can_be_disabled(
    controller_importer,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    state_file = tmp_path / "state.json"
    state_file.write_text('{"values":{}}', encoding="utf-8")
    monkeypatch.setenv("CONFIG_FILE", str(write_proxy_config(tmp_path, state_file, proxy_logs=False)))

    proxy = controller_importer.module("app.start_proxy")
    proxy.log("hidden")

    captured = capsys.readouterr()
    assert captured.out == ""


@pytest.mark.unit
def test_routes_only_include_authenticated_dynamic_tunnels(controller_importer, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    state_file = tmp_path / "state.json"
    state_file.write_text('{"values":{"FRONTEND_PROXY_IP":"10.1.2.3"}}', encoding="utf-8")
    monkeypatch.setenv("CONFIG_FILE", str(write_proxy_config(tmp_path, state_file)))
    monkeypatch.setenv("CALDERA_SERVER", "caldera")
    monkeypatch.setenv("CALDERA_PORT", "8888")
    monkeypatch.setenv("REGISTRY_NAME", "registry")
    monkeypatch.setenv("REGISTRY_PORT", "5000")

    proxy = controller_importer.module("app.start_proxy")
    proxy.DYNAMIC_ROUTES["/custom/8080/"] = ("custom", 8080, True)
    routes = proxy.routes()

    assert "/caldera/" not in routes
    assert "/registry/" not in routes
    assert "/frontend/" not in routes
    assert "/api/" not in routes
    assert routes["/custom/8080/"] == ("custom", 8080, True)


@pytest.mark.unit
def test_dynamic_tunnel_registers_default_http_upstream(
    controller_importer,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_file = tmp_path / "state.json"
    state_file.write_text('{"values":{}}', encoding="utf-8")
    monkeypatch.setenv("CONFIG_FILE", str(write_proxy_config(tmp_path, state_file)))

    proxy = controller_importer.module("app.start_proxy")

    route = proxy.register_dynamic_tunnel("caldera", 8888)

    assert route == "/"
    assert proxy.routes() == {"/": ("caldera", 8888, False)}
    assert proxy.matching_route("/") == ("/", ("caldera", 8888, False))
    assert proxy.matching_route("/login") == ("/", ("caldera", 8888, False))


@pytest.mark.unit
def test_matching_route_prefers_most_specific_prefix(
    controller_importer,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_file = tmp_path / "state.json"
    state_file.write_text('{"values":{}}', encoding="utf-8")
    monkeypatch.setenv("CONFIG_FILE", str(write_proxy_config(tmp_path, state_file)))

    proxy = controller_importer.module("app.start_proxy")
    proxy.DYNAMIC_ROUTES["/"] = ("caldera", 8888, False)
    proxy.DYNAMIC_ROUTES["/custom/"] = ("custom", 8080, True)

    assert proxy.matching_route("/custom/path") == (
        "/custom/",
        ("custom", 8080, True),
    )


@pytest.mark.unit
def test_invalid_kube_api_proxy_target_returns_none(controller_importer, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    state_file = tmp_path / "state.json"
    state_file.write_text('{"values":{"kube_api_proxy_target":{"enabled":true,"host":"","port":"bad"}}}', encoding="utf-8")
    monkeypatch.setenv("CONFIG_FILE", str(write_proxy_config(tmp_path, state_file)))

    proxy = controller_importer.module("app.start_proxy")

    assert proxy.kube_api_proxy_target() is None
