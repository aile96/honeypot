#!/usr/bin/env python3
"""Python entrypoint for remove-pids.sh."""

from __future__ import annotations

import os
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

_SCRIPT = '#!/usr/bin/env bash\nset -euo pipefail\n\nPIDFILE="${1:-$DATA_PATH/KC5/arp_pids}"\nTERM_WAIT_SECONDS="5"\n\nif [[ ! -f "$PIDFILE" ]]; then\n  echo "PID file not found: $PIDFILE" >&2\n  exit 1\nfi\n\n# Function that tries to stop a PID cleanly\nstop_pid() {\n  local pid="$1"\n\n  # Sanity check: PID must be numeric\n  if ! [[ "$pid" =~ ^[0-9]+$ ]]; then\n    echo "[!] Ignore line not numeric: \'$pid\'"\n    return\n  fi\n\n  if ! kill -0 "$pid" 2>/dev/null; then\n    echo "[*] PID $pid doesn\'t exist (already killed)."\n    return\n  fi\n\n  echo "[*] Sending SIGTERM to PID $pid"\n  kill -TERM "$pid" 2>/dev/null || true\n\n  # Wait for termination before TERM_WAIT_SECONDS elapses\n  local end=$(( SECONDS + TERM_WAIT_SECONDS ))\n  while kill -0 "$pid" 2>/dev/null; do\n    if (( SECONDS >= end )); then\n      echo "[*] PID $pid still alive after ${TERM_WAIT_SECONDS}s, sending SIGKILL"\n      kill -KILL "$pid" 2>/dev/null || true\n      break\n    fi\n    sleep 1\n  done\n\n  if kill -0 "$pid" 2>/dev/null; then\n    echo "[!] PID $pid seems to still be running after SIGKILL." >&2\n  else\n    echo "[*] PID $pid terminated."\n  fi\n}\n\n# Read the PID file and stop each PID, one per line\nwhile IFS= read -r line || [[ -n "$line" ]]; do\n  # strip spaces\n  line="${line#"${line%%[![:space:]]*}"}"\n  line="${line%"${line##*[![:space:]]}"}"\n  [[ -z "$line" ]] && continue\n  stop_pid "$line"\ndone < "$PIDFILE"\n\n# Remove the PID file\nrm -f "$PIDFILE" || true\necho "[*] Every PID has been processed. PID file deleted: $PIDFILE"\n\nexit 0'


def _run_embedded_bash(script: str, argv: list[str]) -> int:
    """Run the embedded legacy payload through Bash with a Python-owned entrypoint."""
    try:
        current = Path(__file__).resolve()
        for parent in current.parents:
            lib_dir = parent / "lib"
            if (lib_dir / "common" / "shell.py").is_file():
                sys.path.insert(0, str(lib_dir))
                break
        from common.shell import run_embedded_bash
        return run_embedded_bash(script, argv)
    except Exception:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".sh", delete=False) as handle:
            handle.write(script)
            path = handle.name
        try:
            Path(path).chmod(Path(path).stat().st_mode | stat.S_IXUSR)
            completed = subprocess.run(["/usr/bin/env", "bash", path, *argv], text=True)
            return int(completed.returncode)
        finally:
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass


def main() -> None:
    raise SystemExit(_run_embedded_bash(_SCRIPT, sys.argv[1:]))


if __name__ == "__main__":
    main()
