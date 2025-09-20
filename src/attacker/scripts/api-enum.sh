#!/usr/bin/env bash
set -euo pipefail

# ===== Config =====
API_SERVER="${1:-https://kind-cluster-control-plane:6443}"
NSPROTO="${2:-app}"
NSCREDS="${3:-mem}"
CMNAME="${4:-flagd-credentials-ui}"
FNPROTO="${5:-/tmp/demo.proto}"
FNCREDS="${6:-/tmp/credentials}"


echo "[*] Chiedo all'API /healthz /version /apis /namespaces"
curl -k $API_SERVER/healthz > /dev/null
curl -k $API_SERVER/version > /dev/null
curl -k $API_SERVER/apis > /dev/null
mapfile -t NAMESPACES < <(curl -k $API_SERVER/api/v1/namespaces | jq -r '.items[].metadata.name')

echo "[*] Trovati ${#NAMESPACES[@]} namespace"
# 3. ciclo su ciascun ns e scarico le configmap
for ns in "${NAMESPACES[@]}"; do
  echo "=== Namespace: $ns ==="
  curl -sk "$API_SERVER/api/v1/namespaces/$ns/configmaps" \
    | jq -r '.items[].metadata.name' || true
  echo
done

curl -sk $API_SERVER/api/v1/namespaces/$NSPROTO/configmaps \
  | jq -r '.items[] | select(.metadata.name=="proto") | .data["demo.proto"]' > $FNPROTO

curl -sk "$API_SERVER/api/v1/namespaces/$NSCREDS/configmaps" \
| jq -r --arg CMNAME "$CMNAME" '
  .items[]
  | select(.metadata.name==$CMNAME)
  | .data[]' > "$FNCREDS"

echo "[*] Contatto flagd"
curl -k "$API_SERVER/api/v1/namespaces/$NSCREDS/services/flagd:4000/proxy/feature"