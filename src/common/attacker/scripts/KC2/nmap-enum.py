#!/usr/bin/env python3
"""Python entrypoint for nmap-enum.sh."""

from __future__ import annotations

import os
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

_SCRIPT = r"""#!/usr/bin/env bash

if [ -f ${ATTACKER_LIB_DIR:-/opt/attacker-lib}/common/require-tools.sh ]; then
  # Prefer the shared helper when the script runs inside the attacker image.
  source ${ATTACKER_LIB_DIR:-/opt/attacker-lib}/common/require-tools.sh
else
  # Remote stdin execution may not have the helper file available.
  require_tools() {
    local missing=()
    local tool
    for tool in "$@"; do
      command -v "$tool" >/dev/null 2>&1 || missing+=("$tool")
    done
    if (( ${#missing[@]} > 0 )); then
      echo "Missing required tools: ${missing[*]}" >&2
      exit 1
    fi
  }
fi

OUTDIR="${1:-$DATA_PATH/KC2}"
RMFILE="${2:-0}"

require_tools nmap ip awk getent

# Show the revealed network
NETWORK=$(ip -o -4 addr show | awk 'NR>1{print $4}' | awk -F. 'NF==4{print $1"."$2"."$3".0/24"; exit}')
echo "#NETWORK: $NETWORK"
mkdir -p $OUTDIR

# Network scan (/23 to be faster)
nmap -sn -T4 "$NETWORK" -oG - \
  | awk '/Up$/{print $2}' \
  | while read -r ip; do
      host=$(getent hosts "$ip" | awk '{print $2}')
      if [ -z "$host" ]; then
        host="UNKNOWN"
      fi
      echo "$ip - $host"
    done > $OUTDIR/iphost

cat $OUTDIR/iphost

if [ "${RMFILE}" -eq 1 ]; then
  rm -f "$OUTDIR/iphost"
fi
"""


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
