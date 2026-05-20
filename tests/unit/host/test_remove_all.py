from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import remove_all  # noqa: E402


def test_remove_all_is_idempotent_when_controller_is_missing(monkeypatch, tmp_path):
    calls: list[tuple[str, object]] = []

    monkeypatch.setattr(remove_all, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(sys, "argv", ["remove_all.py"])
    monkeypatch.setattr(
        remove_all,
        "load_project_config",
        lambda _path: {
            "LAB_NAME": "honeypotlab",
            "CONTROLLER_CONTAINER_NAME": "honeypotlab-controller",
            "REGISTRY_CACHE_NAME": "registry-lab",
        },
    )
    monkeypatch.setattr(remove_all, "check_docker", lambda: calls.append(("check_docker", None)))
    monkeypatch.setattr(remove_all, "container_exists", lambda name: False)
    monkeypatch.setattr(remove_all, "remove_runtime_dir", lambda path: calls.append(("runtime", path)))
    monkeypatch.setattr(remove_all, "remove_registry_if_unused", lambda root, name: calls.append(("registry", (root, name))))
    monkeypatch.setattr(remove_all, "remove_network_if_unused", lambda root, name: calls.append(("network", (root, name))))

    assert remove_all.main() == 0
    assert ("runtime", tmp_path / "res" / "runtime" / "honeypotlab") in calls
    assert ("registry", (tmp_path, "registry-lab")) in calls
