#!/usr/bin/env bash
set -euo pipefail

# Endpoint e risorse
ENDPOINT="https://$CLUSTER_NAME-control-plane:6443/api/v1/namespaces/$NSCREDS/services/traffic-controller:8080/proxy/translate"
TARGET='payment.pay.svc.cluster.local:8080'
METHOD='oteldemo.PaymentService/ReceivePayment'
PROTO_FILE="$DATA_PATH/KC3/demo.proto"

# Installing dependencies and setup
apt-get update >/dev/null 2>&1
apt-get install -y --no-install-recommends bash curl jq ca-certificates >/dev/null 2>&1
mkdir -p $(dirname $PROTO_FILE)

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
