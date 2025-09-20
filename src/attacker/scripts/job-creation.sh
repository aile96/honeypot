#!/usr/bin/env bash
set -euo pipefail

# ===== Config =====
API_SERVER="${1:-https://kind-cluster-control-plane:6443}"
NS="${2:-app}"
SECRET="${3:-dbcurrency-creds}"
JOB_NAME="insert-currency-rate-$(date +%Y%m%d%H%M%S)"
PGHOST="postgres.dat.svc.cluster.local"
PGPORT="5432"
PGDATABASE="currency"
SQL_STMT="INSERT INTO currency_rates (code, rate) VALUES ('NUL', 0);"
TOKEN="$(cat /tmp/tokenCurrency)"

echo ">> Leggo il Secret ${SECRET} nel namespace ${NS}..."
SECRET_JSON=$(curl -sk \
  --header "Authorization: Bearer $TOKEN" \
  $API_SERVER/api/v1/namespaces/$NS/secrets/$SECRET)
# Estrazione e decodifica credenziali
USER="$(jq -r '.data.username' <<<"$SECRET_JSON" | base64 -d)"
PASS="$(jq -r '.data.password' <<<"$SECRET_JSON" | base64 -d)"

echo ">> Credenziali estratte (username/password decodificate $USER - $PASS)."

JOB_JSON="$(
  jq -n \
    --arg name "$JOB_NAME" \
    --arg ns "$NS" \
    --arg pghost "$PGHOST" \
    --arg pgport "$PGPORT" \
    --arg pgdb "$PGDATABASE" \
    --arg pguser "$USER" \
    --arg pgpass "$PASS" \
    --arg sql "$SQL_STMT" '
{
  apiVersion: "batch/v1",
  kind: "Job",
  metadata: {
    name: $name,
    namespace: $ns,
    labels: { "app.kubernetes.io/name": "insert-currency-rate" }
  },
  spec: {
    backoffLimit: 0,
    ttlSecondsAfterFinished: 300,
    template: {
      spec: {
        restartPolicy: "Never",
        containers: [
          {
            name: "psql",
            image: "postgres:16",
            env: [
              { name: "PGHOST", value: $pghost },
              { name: "PGPORT", value: $pgport },
              { name: "PGDATABASE", value: $pgdb },
              { name: "PGUSER", value: $pguser },
              { name: "PGPASSWORD", value: $pgpass },
              { name: "SQL", value: $sql }
            ],
            command: [
              "sh","-c",
              "psql \"host=$PGHOST port=$PGPORT dbname=$PGDATABASE user=$PGUSER password=$PGPASSWORD sslmode=disable\" -v ON_ERROR_STOP=1 -c \"$SQL\""
            ]
          }
        ]
      }
    }
  }
}
'
)"

MANIFEST_FILE="${JOB_NAME}.json"
printf '%s\n' "$JOB_JSON" > "$MANIFEST_FILE"
echo ">> Manifest salvato in ${MANIFEST_FILE}"

echo ">> Creo il Job ${JOB_NAME} nel namespace ${NS}..."
CREATE_RESP="$(
  curl -sSk -X POST \
    --header "Authorization: Bearer ${TOKEN}" \
    -H "Content-Type: application/json" \
    -d @"${MANIFEST_FILE}" \
    "${API_SERVER}/apis/batch/v1/namespaces/${NS}/jobs"
)"

if echo "$CREATE_RESP" | jq -e '.kind=="Job"' >/dev/null 2>&1; then
  echo "Job creato: ${JOB_NAME}"
else
  echo "Errore creando il Job. Risposta API:"
  echo "$CREATE_RESP" | jq .
  exit 1
fi
