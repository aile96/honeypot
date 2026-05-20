from __future__ import annotations

from pathlib import Path

import pytest


@pytest.mark.unit
def test_optional_config_bool_accepts_extended_boolean_values(controller_importer) -> None:
    kind_template = controller_importer.module("lib.kind_template")

    assert kind_template.optional_config_bool({"OPEN_PORTS": "enabled"}, "OPEN_PORTS") is True
    assert kind_template.optional_config_bool({"OPEN_PORTS": "disabled"}, "OPEN_PORTS", True) is False
    assert kind_template.optional_config_bool({}, "OPEN_PORTS", True) is True
    with pytest.raises(SystemExit):
        kind_template.optional_config_bool({"OPEN_PORTS": "maybe"}, "OPEN_PORTS")


@pytest.mark.unit
def test_registry_and_worker_node_blocks(controller_importer) -> None:
    kind_template = controller_importer.module("lib.kind_template")
    config = {"KIND_CONTAINERD_CERTS_DIR": "/certs", "OPEN_PORTS": True}

    mount = kind_template.registry_extra_mounts_block(config)
    workers = kind_template.worker_nodes_block(config, 2, include_open_ports=True)

    assert "hostPath: /certs" in mount
    assert workers.count("- role: worker") == 2
    assert "read-only-port: \"10255\"" in workers


@pytest.mark.unit
def test_prepare_kind_template_defaults_writes_config_and_state(controller_importer) -> None:
    kind_template = controller_importer.module("lib.kind_template")
    config = {
        "LAB_NAME": "honeypotlab",
        "REGISTRY_NAME": "registry",
        "REGISTRY_PORT": 5000,
        "WORKERS": 2,
        "CILIUM_ENABLED": True,
    }
    state = {"values": {}}

    kind_template.prepare_kind_template_defaults(config, state, default_workers=1)

    assert config["KIND_REGISTRY_ENDPOINT"] == "registry:5000"
    assert config["KIND_DISABLE_DEFAULT_CNI_BLOCK"] == "  disableDefaultCNI: true\n"
    assert config["KIND_WORKER_NODES_BLOCK"].count("- role: worker") == 2
    assert state["values"]["kind_default_template_variables"]["LAB_NAME"] == "honeypotlab"


@pytest.mark.unit
def test_control_plane_patch_template_variables_for_etcd_and_anonymous_auth(controller_importer) -> None:
    kind_template = controller_importer.module("lib.kind_template")
    config = {
        "ETCD_EXPOSURE": True,
        "PLAIN_PORT": 12379,
        "ETCD_EXPOSURE_HOST_PORT": 12379,
        "ANONYMOUS_AUTH": True,
    }
    state = {"values": {}}

    kind_template.prepare_control_plane_patch_template_variables(config, state, include_anonymous_auth=True)

    assert "listen-client-urls" in config["KIND_ETCD_UNAUTHENTICATED_KUBEADM_CONFIG_PATCH_BLOCK"]
    assert "extraPortMappings" in config["KIND_ETCD_UNAUTHENTICATED_PORT_MAPPING_BLOCK"]
    assert "anonymous-auth" in config["KIND_ETCD_UNAUTHENTICATED_KUBEADM_CONFIG_PATCHES_BLOCK"]
    assert state["values"]["kind_etcd_exposure"]["enabled"] is True
    assert state["values"]["kind_anonymous_auth"]["enabled"] is True


@pytest.mark.unit
def test_kind_create_wait_is_disabled_when_default_cni_is_disabled(controller_importer) -> None:
    kind_step = controller_importer.script(
        "kind_pipeline_step",
        Path("src/lab/lab-controller/app/pipeline/01_kind.py").resolve(),
    )

    assert kind_step.kind_create_wait_duration({"KIND_CREATE_WAIT": "45s", "CILIUM_ENABLED": True}) == "45s"
    assert kind_step.kind_create_wait_duration({"KIND_DISABLE_DEFAULT_CNI_BLOCK": "  disableDefaultCNI: true\n"}) == "0s"
    assert kind_step.kind_create_wait_duration({"CILIUM_ENABLED": True}) == "0s"
    assert kind_step.kind_create_wait_duration({"CILIUM_ENABLED": False}) == "180s"
