#!/usr/bin/env bash
set -euo pipefail


# Generating a temporary file in /tmp
OUT=$(mktemp -p /tmp envoy.XXXX.yaml)

envsubst '${LISTEN_PORT} ${APP_PORT}' \
  < /etc/envoy/envoy.yaml.tpl > "$OUT"

export CHECKOUT_PORT="${APP_PORT}"

/usr/src/app/checkout & APP_PID=$!
/usr/local/bin/envoy -c "$OUT" --log-level info & ENVOY_PID=$!

wait -n "$APP_PID" "$ENVOY_PID"
kill "$APP_PID" "$ENVOY_PID" 2>/dev/null || true
wait || true