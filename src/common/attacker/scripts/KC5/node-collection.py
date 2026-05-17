#!/usr/bin/env python3
"""Python entrypoint for node-collection.sh."""

from __future__ import annotations

import os
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

_SCRIPT = r"""#!/usr/bin/env bash
set -euo pipefail

FILE_IP="/tmp/iphost"
KEY_PATH="$DATA_PATH/KC5/ssh/ssh-key"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NODE_IP_HELPER="${ATTACKER_LIB_DIR:-/opt/attacker-lib}/common/list-node-ips.sh"

[[ -f "${NODE_IP_HELPER}" ]] || { echo "Missing helper: ${NODE_IP_HELPER}" >&2; exit 1; }
# shellcheck source=../../lib/common/list-node-ips.sh
source "${NODE_IP_HELPER}"

SSH_OPTS=(
  -p 2222
  -o StrictHostKeyChecking=accept-new
  -o BatchMode=yes
  -o ConnectTimeout=5
)

timestamp() { date +"%Y-%m-%d %H:%M:%S"; }

# Wait until the node is reachable via SSH
wait_for_ssh() {
  local ip="$1"
  local timeout_sec="${2:-300}"   # default: 5 minutes
  local start now elapsed sleep_s=2 attempt=0

  start=$(date +%s)

  while : ; do
    attempt=$((attempt+1))
    # Simple SSH probe that runs `true` remotely
    if ssh "${SSH_OPTS[@]}" -i "$KEY_PATH" "root@$ip" true </dev/null 2>/dev/null; then
      echo "$(timestamp) >> $ip: SSH is ready (attempt $attempt)"
      return 0
    fi

    now=$(date +%s)
    elapsed=$((now - start))
    if (( elapsed >= timeout_sec )); then
      echo "$(timestamp) >> $ip: SSH not reachable after ${elapsed}s (attempts: $attempt)" >&2
      return 1
    fi

    echo "$(timestamp) >> $ip: SSH not ready yet (attempt $attempt). Retrying..."
    sleep "$sleep_s"
    # Exponential backoff up to 20s
    (( sleep_s < 20 )) && sleep_s=$(( sleep_s * 2 ))
  done
}

mapfile -t nodes < <(list_worker_node_ips "$FILE_IP" | sed '/^$/d')
if [[ "${#nodes[@]}" -eq 0 ]]; then
  echo "No node found"; exit 1
fi

echo ">> Nodes found (${#nodes[@]}): ${nodes[*]}"

for n in "${nodes[@]}"; do
  # Wait for SSH to become reachable
  if ! wait_for_ssh "$n" 600; then
    echo "SKIP: $n is not reachable, moving on" >&2
    continue
  fi

  # Run the remote job only after SSH is ready
  echo "$(timestamp) >> Starting analysis on $n"
  if ssh "${SSH_OPTS[@]}" -i "$KEY_PATH" "root@$n" '/usr/bin/env python3 -' -- "0" \
       < /opt/caldera/KC4/container-collection.py \
       > "$DATA_PATH/KC5/container-collection-$n.log" 2>&1
  then
    echo "Analysis completed for $n"
  else
    rc=$?
    echo "$(timestamp) >> ERROR: analysis on $n failed (rc=$rc) — check $DATA_PATH/KC5/container-collection-$n.log" >&2
  fi
done
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
