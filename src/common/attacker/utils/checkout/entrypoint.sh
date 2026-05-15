#!/usr/bin/env bash
set -euo pipefail

# Generate temporary Envoy config file
OUT=$(mktemp -p /tmp envoy.XXXX.yaml)

# Replace placeholders (important: include DNS_WORKER_PORT)
envsubst '${LISTEN_PORT} ${APP_PORT} ${DNS_WORKER_PORT}' \
  < /etc/envoy/envoy.yaml.tpl > "$OUT"

export CHECKOUT_PORT="${APP_PORT}"

# Start DNS worker (Python service)
python3 /usr/src/app/dns_worker.py & DNS_WORKER_PID=$!

# Start main app
/usr/src/app/checkout & APP_PID=$!

# Start Envoy proxy
/usr/local/bin/envoy -c "$OUT" --log-level info & ENVOY_PID=$!

# Wait until one exits, then clean up
wait -n "$DNS_WORKER_PID" "$APP_PID" "$ENVOY_PID"
kill "$DNS_WORKER_PID" "$APP_PID" "$ENVOY_PID" 2>/dev/null || true
wait || true
