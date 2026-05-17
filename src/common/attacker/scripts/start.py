#!/usr/bin/env python3
"""Python entrypoint for start.sh."""

from __future__ import annotations

import os
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

_SCRIPT = r"""#!/usr/bin/env bash
set -euo pipefail

CALDERA_URL="${CALDERA_URL:-http://caldera.dock:8888}"
GROUP="${GROUP:-cluster}"
CALDERA_WAIT_TIMEOUT_SEC="${CALDERA_WAIT_TIMEOUT_SEC:-300}"
CALDERA_WAIT_INTERVAL_SEC="${CALDERA_WAIT_INTERVAL_SEC:-2}"
SANDCAT_PATH="${SANDCAT_PATH:-/tmp/sandcat}"
SANDCAT_PID=""

case "${WAIT:-1}" in
  1|true|TRUE|yes|YES) START_DELAY_SEC=60 ;;
  0|false|FALSE|no|NO) START_DELAY_SEC=10 ;;
  *) START_DELAY_SEC=10 ;;
esac

is_reachable() {
  if command -v wget >/dev/null 2>&1; then
    wget -qO- "$1" >/dev/null
    return $?
  fi
  if command -v curl >/dev/null 2>&1; then
    curl -fsS -o /dev/null -X GET "$1"
    return $?
  fi
  echo "Neither wget nor curl is available." >&2
  return 1
}

download_sandcat() {
  if command -v wget >/dev/null 2>&1; then
    wget -qO "${SANDCAT_PATH}" "${CALDERA_URL}/file/download" \
      --header='file:sandcat.go' \
      --header='platform:linux' \
      --header="server:${CALDERA_URL}" \
      --header="group:${GROUP}"
    return $?
  fi
  if command -v curl >/dev/null 2>&1; then
    curl -fsS -o "${SANDCAT_PATH}" "${CALDERA_URL}/file/download" \
      -H 'file:sandcat.go' \
      -H 'platform:linux' \
      -H "server:${CALDERA_URL}" \
      -H "group:${GROUP}"
    return $?
  fi
  echo "Neither wget nor curl is available." >&2
  return 1
}

CALDERA_URL="${CALDERA_URL%/}"
echo "Waiting for ${CALDERA_URL} (timeout: ${CALDERA_WAIT_TIMEOUT_SEC}s) ..."
start_ts="$(date +%s)"
until is_reachable "${CALDERA_URL}"; do
  now_ts="$(date +%s)"
  if (( now_ts - start_ts >= CALDERA_WAIT_TIMEOUT_SEC )); then
    echo "Timed out waiting for ${CALDERA_URL} after ${CALDERA_WAIT_TIMEOUT_SEC}s" >&2
    exit 1
  fi
  sleep "${CALDERA_WAIT_INTERVAL_SEC}"
done

sleep "${START_DELAY_SEC}"

echo "Downloading sandcat payload..."
download_sandcat

chmod +x "${SANDCAT_PATH}"

echo "Starting sandcat agent..."
"${SANDCAT_PATH}" &
SANDCAT_PID="$!"

echo "Sandcat agent started with PID ${SANDCAT_PID}; start.sh completed."
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
