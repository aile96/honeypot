#!/usr/bin/env python3
"""Remove stale OpenTelemetry underlay containers before Compose startup."""

from lib import compose_down, compose_environment, log


def main() -> None:
    log("Removing any existing Compose underlay containers before startup.")
    compose_down(CONFIG, env=compose_environment(CONFIG))


if __name__ == "__main__":
    main()
