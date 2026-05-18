#!/usr/bin/env python3
"""OpenTelemetry kill-chain-specific restore entrypoint."""

from pathlib import Path
import runpy

runpy.run_path(str(Path(__file__).with_name("HOOK_RESTORE.py")), run_name="__main__", init_globals=globals())
