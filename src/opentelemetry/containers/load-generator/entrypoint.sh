#!/usr/bin/env bash
set -euo pipefail

WAIT_FOR_URLS="${LOCUST_HOST}/api/products/6E92ZMYYFZ"
WAIT_TIMEOUT="18000"
WAIT_INTERVAL="60"

echo "Waiting for: ${WAIT_FOR_URLS} (timeout=${WAIT_TIMEOUT}s)"
deadline=$(( $(date +%s) + WAIT_TIMEOUT ))
IFS=',' read -ra URLS <<< "${WAIT_FOR_URLS}"
for raw in "${URLS[@]}"; do
  url="$(echo "$raw" | xargs)"
  until curl -fsS --max-time 2 "$url" >/dev/null; do
    if [ "$(date +%s)" -ge "$deadline" ]; then
      echo "TIMEOUT: $url"; exit 1
    fi
    sleep "${WAIT_INTERVAL}"
  done
  echo "OK: $url"
done

exec locust --skip-log-setup
