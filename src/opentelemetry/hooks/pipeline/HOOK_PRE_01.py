#!/usr/bin/env python3
"""Prepare OpenTelemetry-specific Kind template variables."""

from lib import (
    kind_cluster_name,
    log,
    prepare_control_plane_patch_template_variables,
    prepare_kind_template_defaults,
)


def main() -> None:
    """Prepare Kind template variables for the OpenTelemetry target."""
    log("Preparing default Kind template variables.")
    prepare_kind_template_defaults(
        CONFIG,
        STATE,
        profile_name=kind_cluster_name(CONFIG),
        default_workers=2,
        minimum_workers=2,
        include_open_ports=True,
    )
    prepare_control_plane_patch_template_variables(CONFIG, STATE, include_anonymous_auth=True)


if __name__ == "__main__":
    main()
