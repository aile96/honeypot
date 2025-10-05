#!/usr/bin/env bash
set -euo pipefail

# Endpoint e risorse
ENDPOINT="$API_SERVER/api/v1/namespaces/$NSCREDS/services/traffic-controller:8080/proxy/translate"
TARGET='payment.pay.svc.cluster.local:8080'
METHOD='oteldemo.PaymentService/ReceivePayment'
PROTO_FILE="$DATA_PATH/KC3/demo.proto"

# Legge il contenuto del proto e lo converte in stringa JSON correttamente escapata
PROTO_JSON="$(jq -Rs . < "$PROTO_FILE")"

# Pulisce/crea il file di output
OUT_FILE="$DATA_PATH/KC3//result_payments"

echo "Inizio richieste (user_id 1..15) → salvataggio in $OUT_FILE"

for uid in $(seq 1 15); do
  # Costruisce il body JSON in modo robusto con jq
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

  # Effettua la POST
  RESP="$(curl -sk -X POST "$ENDPOINT" \
    -H "Content-Type: application/json" \
    -d "$BODY")"

  # Estrae solo .payment (se presente) e aggiunge una riga NDJSON
  if echo "$RESP" | jq -e '.stdout.payment' >/dev/null 2>&1; then
    echo "$RESP" | jq -c '.stdout.payment' >> "$OUT_FILE"
    echo "✔ user_id=$uid salvato"
  else
    # Logga eventuali errori/assenza payment
    ERR_MSG="$(echo "$RESP" | jq -r '.error // empty' 2>/dev/null || true)"
    echo "⚠ user_id=$uid: nessun campo .payment trovato${ERR_MSG:+ (errore: $ERR_MSG)}" >&2
  fi
done

echo "Fatto. File generato: $OUT_FILE"
