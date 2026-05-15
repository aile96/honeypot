#!/usr/bin/env python3
"""Finalize the 5Gcore deployment after Skaffold completes.

The generic Skaffold step may have used a temporary Docker build-helper daemon to
build and push images to the local HTTPS registry. This hook removes that helper
when it was started and leaves a clear completion message in the pipeline logs."""

from lib import get_state_value, log, stop_cluster_build_helper


def main() -> None:
    if get_state_value(STATE, "skaffold_build_helper_started", False):
        stop_cluster_build_helper(CONFIG)
    log("5Gcore Skaffold finalization completed.")


if __name__ == "__main__":
    main()
