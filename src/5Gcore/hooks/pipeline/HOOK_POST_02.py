#!/usr/bin/env python3
"""Record the 5Gcore helper-image strategy after Compose starts.

The 5Gcore target does not publish extra helper images in this phase because
Skaffold builds and pushes all Kubernetes images directly from src/5Gcore/containers.
The hook records that decision in STATE so later steps and debug output stay explicit."""

from lib import log, set_state_value


def main() -> None:
    set_state_value(STATE, "fivegcore_helper_images", [])
    log("5Gcore init containers use public helper images; no local helper publish needed.")


if __name__ == "__main__":
    main()
