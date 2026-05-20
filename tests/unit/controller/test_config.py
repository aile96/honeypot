from __future__ import annotations

import os
import tomllib
from pathlib import Path

import pytest


@pytest.mark.unit
def test_save_and_load_config_file_round_trip(controller_importer, tmp_path: Path) -> None:
    config_mod = controller_importer.module("lib.config")
    path = tmp_path / "runtime" / "config.toml"

    config_mod.save_config_file({"LAB_NAME": "honeypotlab", "HOST_SOCKET": False, "PORT": 18080}, path)
    loaded = config_mod.load_config_file(path)

    assert loaded == {"LAB_NAME": "honeypotlab", "HOST_SOCKET": False, "PORT": 18080}
    assert tomllib.loads(path.read_text(encoding="utf-8"))["config"]["LAB_NAME"] == "honeypotlab"


@pytest.mark.unit
def test_load_runtime_config_overlays_only_allowed_environment(
    controller_importer,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_mod = controller_importer.module("lib.config")
    path = tmp_path / "config.toml"
    config_mod.save_config_file({"LAB_NAME": "from-file", "CLUSTER_TARGET": "5Gcore"}, path)
    monkeypatch.setattr(config_mod, "runtime_config_file", lambda: path)
    monkeypatch.setenv("LAB_NAME", "from-env")
    monkeypatch.setenv("CLUSTER_TARGET", "opentelemetry")

    no_overlay = config_mod.load_runtime_config(overlay_env=False)
    overlay = config_mod.load_runtime_config(overlay_env=True, allowed_env=("LAB_NAME",))

    assert no_overlay["LAB_NAME"] == "from-file"
    assert no_overlay["CLUSTER_TARGET"] == "5Gcore"
    assert overlay["LAB_NAME"] == "from-env"
    assert overlay["CLUSTER_TARGET"] == "5Gcore"


@pytest.mark.unit
@pytest.mark.parametrize(
    ("raw", "expected"),
    [(True, True), ("true", True), ("1", True), ("yes", True), (False, False), ("false", False), ("0", False), ("off", False)],
)
def test_parse_bool_value_accepts_common_shell_booleans(controller_importer, raw, expected) -> None:
    config_mod = controller_importer.module("lib.config")

    assert config_mod.parse_bool_value(raw, name="FLAG") is expected


@pytest.mark.unit
def test_config_int_enforces_bounds(controller_importer) -> None:
    config_mod = controller_importer.module("lib.config")

    assert config_mod.config_int({"PORT": "18080"}, "PORT", minimum=1, maximum=65535) == 18080
    with pytest.raises(SystemExit):
        config_mod.config_int({"PORT": "0"}, "PORT", minimum=1)
    with pytest.raises(SystemExit):
        config_mod.config_int({"PORT": "abc"}, "PORT")


@pytest.mark.unit
def test_config_to_env_stringifies_without_mutating_os_environ(controller_importer, monkeypatch: pytest.MonkeyPatch) -> None:
    config_mod = controller_importer.module("lib.config")
    monkeypatch.delenv("HOST_SOCKET", raising=False)

    env = config_mod.config_to_env({"HOST_SOCKET": False, "COUNT": 3}, base_env={"PATH": os.environ.get("PATH", "")})

    assert env["HOST_SOCKET"] == "false"
    assert env["COUNT"] == "3"
    assert "HOST_SOCKET" not in os.environ


@pytest.mark.unit
def test_substitute_vars_preserves_compose_escaped_placeholders(controller_importer) -> None:
    utils = controller_importer.module("lib.utils")

    rendered = utils.substitute_vars(
        'lab=${LAB_NAME} command="chown -R $${SAMBA_UID}:$${SAMBA_GID} /share"',
        {"LAB_NAME": "honeypotlab"},
    )

    assert rendered == 'lab=honeypotlab command="chown -R $${SAMBA_UID}:$${SAMBA_GID} /share"'
