#!/usr/bin/env sh
set -e

echo "== auto-starter =="
echo "CALDERA_URL=${CALDERA_URL:-http://localhost:8888}"
echo "GROUP=${GROUP:-cluster}"
echo "ADV_LIST=${ADV_LIST:-KC1 – Safe Mining Emulation}"
if [ -n "$CALDERA_KEY" ]; then echo "CALDERA_KEY set (hidden)"; else echo "CALDERA_KEY not set"; fi
echo "==================="

exec python /app/auto_starter.py
