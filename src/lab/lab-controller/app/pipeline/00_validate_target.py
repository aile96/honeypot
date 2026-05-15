#!/usr/bin/env python3
"""Validate the selected target before the pipeline performs side effects.

This step checks that the target repository has the expected configuration files,
verifies required host tools, and records validation details in STATE. It is kept
separate from target hooks so invalid inputs fail before Compose, Kind, or
Skaffold are allowed to change the local machine."""

from pathlib import Path

from lib import (
    config_bool,
    config_list,
    log,
    require_command,
    run_cmd,
    set_state_value,
    target_root,
    warn,
)

DEFAULT_REQUIRED_FILES = [
    "conf-files/variables.py",
    "conf-files/compose.yaml",
    "conf-files/kind-cluster.yaml.tmpl",
    "conf-files/skaffold.yaml.tmpl",
]

DEFAULT_REQUIRED_DIRS = [
    "containers",
    "conf-files",
    "helm-charts",
]

DEFAULT_REQUIRED_COMMANDS = ["docker", "kind", "kubectl", "helm", "skaffold"]


def configured_commands() -> list[str]:
    return config_list(CONFIG, "REQUIRED_HOST_COMMANDS", DEFAULT_REQUIRED_COMMANDS)


def required_files() -> list[str]:
    return config_list(CONFIG, "REQUIRED_TARGET_FILES", DEFAULT_REQUIRED_FILES)


def required_dirs() -> list[str]:
    dirs = config_list(CONFIG, "REQUIRED_TARGET_DIRS", DEFAULT_REQUIRED_DIRS)

    if config_bool(CONFIG, "CALDERA_SERVER_ENABLE", False) and "caldera" not in dirs:
        dirs.append("caldera")

    return dirs


def validate_existing_paths(root: Path) -> tuple[list[str], list[str]]:
    missing_files = [
        name for name in required_files()
        if not (root / name).is_file()
    ]

    missing_dirs = [
        name for name in required_dirs()
        if not (root / name).is_dir()
    ]

    return missing_files, missing_dirs


def main() -> None:
    log("Running target preflight checks.")

    root = target_root(CONFIG)

    if not root.is_dir():
        raise SystemExit(f"Target root not found or not a directory: {root}")

    missing_files, missing_dirs = validate_existing_paths(root)

    if missing_files or missing_dirs:
        details: list[str] = []

        if missing_files:
            details.append(
                "files: " + ", ".join(str(root / item) for item in missing_files)
            )

        if missing_dirs:
            details.append(
                "directories: " + ", ".join(str(root / item) for item in missing_dirs)
            )

        raise SystemExit(
            "Target layout validation failed; missing " + "; ".join(details)
        )

    commands = configured_commands()

    for command in commands:
        require_command(command)

    if config_bool(CONFIG, "PREFLIGHT_CHECK_DOCKER", True):
        run_cmd(["docker", "info"], check=True, quiet=True, config=CONFIG)
    else:
        warn("Skipping docker info preflight because PREFLIGHT_CHECK_DOCKER=false.")

    set_state_value(STATE, "target_root", str(root))
    set_state_value(STATE, "validated_target_files", required_files())
    set_state_value(STATE, "validated_target_dirs", required_dirs())
    set_state_value(STATE, "preflight_required_commands", commands)

    log("Target preflight checks completed.")


if __name__ == "__main__":
    main()
