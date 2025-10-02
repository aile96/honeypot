#!/usr/bin/env bash
set -euo pipefail

# ===== Config =====
API_SERVER="${API_SERVER:-https://kind-cluster-control-plane:6443}"
NSPROTO="${NSPROTO:-app}"
NSCREDS="${NSCREDS:-mem}"
CMNAME="${CMNAME:-flagd-credentials-ui}"

FNPROTO="$DATA_PATH/KC3/demo.proto"
FNCREDS="$DATA_PATH/KC3/credentials"

mkdir -p $DATA_PATH/KC3

echo "[*] Chiedo all'API /healthz /version /apis /namespaces"
curl -k $API_SERVER/healthz > /dev/null
curl -k $API_SERVER/version > /dev/null
curl -k $API_SERVER/apis > /dev/null
mapfile -t NAMESPACES < <(curl -k $API_SERVER/api/v1/namespaces | jq -r '.items[].metadata.name')

echo "[*] Trovati ${#NAMESPACES[@]} namespace"
# 3. ciclo su ciascun ns e scarico le configmap
for ns in "${NAMESPACES[@]}"; do
  echo "=== Namespace: $ns ==="
  if ! resp="$(curl -fsS "$API_SERVER/api/v1/namespaces/$ns/configmaps" 2>/dev/null)"; then
    echo "  (skip: curl failed)"
    continue
  fi
  # stampa i nomi; se JSON vuoto/non valido, non fallire
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

echo "[*] Contatto flagd"
curl -k "$API_SERVER/api/v1/namespaces/$NSCREDS/services/flagd:4000/proxy/feature"