#!/usr/bin/env python3
"""Prepare 5Gcore-specific Kind template variables."""

from lib import (
    kind_cluster_name,
    log,
    prepare_control_plane_patch_template_variables,
    prepare_kind_template_defaults,
)


def main() -> None:
    """Prepare Kind template variables for the 5Gcore target."""
    log("Preparing default Kind template variables.")
    prepare_kind_template_defaults(
        CONFIG,
        STATE,
        profile_name=kind_cluster_name(CONFIG),
        default_workers=1,
        minimum_workers=1,
        include_open_ports=False,
    )
    prepare_control_plane_patch_template_variables(CONFIG, STATE, include_anonymous_auth=False)


if __name__ == "__main__":
    main()
