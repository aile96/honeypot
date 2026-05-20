#!/usr/bin/env python3
"""Prepare only the directories and variables required by the pipeline."""

from pathlib import Path

from lib import config_bool, config_str, log, set_state_value


def ensure_dir(name: str) -> str:
    path = Path(config_str(CONFIG, name, allow_empty=False))
    path.mkdir(parents=True, exist_ok=True)
    return str(path)


def configure_registry_resolution() -> None:
    """Keep the internal registry name local in the controller-dockerd case."""
    if config_bool(CONFIG, "HOST_SOCKET", False):
        return
    CONFIG.setdefault("REGISTRY_CACHE_ENDPOINT", "registry-lab:5000")
    CONFIG["REGISTRY_HOST_ALIAS"] = "localhost"
    set_state_value(STATE, "registry_resolution", {"registry": "localhost", "mode": "internal-dockerd"})


def main() -> None:
    created = {
        "RUNTIME_DIR": ensure_dir("RUNTIME_DIR"),
        "GENERATED_DIR": ensure_dir("GENERATED_DIR"),
        "RESULTS_DIR": ensure_dir("RESULTS_DIR"),
        "STATE_DIR": ensure_dir("STATE_DIR"),
    }
    configure_registry_resolution()
    set_state_value(STATE, "prepared_directories", created)
    log("Pipeline prepare step completed.")


if __name__ == "__main__":
    main()
