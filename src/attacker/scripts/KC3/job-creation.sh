#!/usr/bin/env bash
set -euo pipefail

# ===== Config =====
API_SERVER="https://$CONTROL_PLANE_NODE:$CONTROL_PLANE_PORT"
NS="${NSPROTO:-app}"

SECRET="dbcurrency-creds"
JOB_NAME="insert-currency-rate-$(date +%Y%m%d%H%M%S)"
PGHOST="postgres.dat.svc.cluster.local"
PGPORT="5432"
PGDATABASE="currency"
SQL_STMT="INSERT INTO currency (code, rate) VALUES ('NUL', 0);"
TOKEN="$(cat $DATA_PATH/KC3/tokenCurrency)"

# Installing dependencies and setup
apt-get update >/dev/null 2>&1
apt-get install -y --no-install-recommends bash curl jq ca-certificates >/dev/null 2>&1
mkdir -p $DATA_PATH/KC3

echo ">> Reading ${SECRET} in namespace ${NS}..."
SECRET_JSON=$(curl -sk \
  --header "Authorization: Bearer $TOKEN" \
  $API_SERVER/api/v1/namespaces/$NS/secrets/$SECRET)
# Extraction credentials
USER="$(jq -r '.data.username' <<<"$SECRET_JSON" | base64 -d)"
PASS="$(jq -r '.data.password' <<<"$SECRET_JSON" | base64 -d)"

echo ">> Credenzials extracted (username/password $USER - $PASS)."

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
            image: "postgres:16-alpine",
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

MANIFEST_FILE="$DATA_PATH/KC3/$JOB_NAME.json"
printf '%s\n' "$JOB_JSON" > "$MANIFEST_FILE"
echo ">> Manifest saved in ${MANIFEST_FILE}"

echo ">> Creating Job ${JOB_NAME} in namespace ${NS}..."
CREATE_RESP="$(
  curl -sSk -X POST \
    --header "Authorization: Bearer ${TOKEN}" \
    -H "Content-Type: application/json" \
    -d @"${MANIFEST_FILE}" \
    "${API_SERVER}/apis/batch/v1/namespaces/${NS}/jobs"
)"

if echo "$CREATE_RESP" | jq -e '.kind=="Job"' >/dev/null 2>&1; then
  echo "Job created: ${JOB_NAME}"
else
  echo "Error creating the Job. API answer:"
  echo "$CREATE_RESP" | jq .
  exit 1
fi
