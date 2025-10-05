#!/usr/bin/env bash
set -euo pipefail

PIDFILE="$DATA_PATH/KC2/arp_pids"
TIME_DOS=60

echo "DOS Abilitato per $TIME_DOS secondi"
sysctl -w net.ipv4.ip_forward=0 >/dev/null

echo "Rimuovo arp spoofing..."
/opt/caldera/common/remove-pids.sh "$PIDFILE" || echo "[WARN] remove-pids fallita" >&2