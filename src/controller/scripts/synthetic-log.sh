#!/usr/bin/env sh
# Invia log direttamente a OpenSearch (porta 9200) e si ferma quando il token compare.
set -eu

#################################
# Configurazione
#################################
OPENSEARCH_URL="${OPENSEARCH_URL:-http://opensearch.mem:9200}" # es. http(s)://host:9200
INDEX_NAME="${INDEX_NAME:-logs-otel}"                          # indice di destinazione
# Autenticazione verso OpenSearch: usa UNO dei due metodi qui sotto (oppure nessuno se non serve)
OPENSEARCH_AUTH_HEADER="${OPENSEARCH_AUTH_HEADER:-}"           # es. "Authorization: Bearer <JWT>" o "Authorization: Basic <base64>"
OPENSEARCH_USER="${OPENSEARCH_USER:-}"                         # alternativa: basic user
OPENSEARCH_PASS="${OPENSEARCH_PASS:-}"                         # alternativa: basic pass
OPENSEARCH_INSECURE="${OPENSEARCH_INSECURE:-false}"            # true per -k se TLS self-signed

# Dati del log sintetico
OTEL_SERVICE_NAME="${OTEL_SERVICE_NAME:-traffic-controller}"
LOG_INTERVAL_SECONDS="${LOG_INTERVAL_SECONDS:-5}"
LOG_SEVERITY_TEXT="${LOG_SEVERITY_TEXT:-Info}"
LOG_SEVERITY_NUMBER="${LOG_SEVERITY_NUMBER:-9}"
TOKEN_FILE="${TOKEN_FILE:-/var/run/secrets/kubernetes.io/serviceaccount/token}"

# Attesa iniziale (per dare tempo a OpenSearch di essere pronto)
INITIAL_SLEEP="${INITIAL_SLEEP:-30}"

#################################
# Funzioni di utilità
#################################
curl_insecure_flag() {
  case "$1" in
    true|TRUE|1) printf "%s" "-k" ;;
    *)           printf "%s" ""    ;;
  esac
}

curl_auth_args() {
  # Ritorna argomenti curl per auth (header Bearer/Basic OPPURE -u user:pass)
  if [ -n "${OPENSEARCH_AUTH_HEADER}" ]; then
    printf -- "-H" ; printf " %s" "${OPENSEARCH_AUTH_HEADER}"
  elif [ -n "${OPENSEARCH_USER}" ] || [ -n "${OPENSEARCH_PASS}" ]; then
    printf -- "-u" ; printf " %s:%s" "${OPENSEARCH_USER}" "${OPENSEARCH_PASS}"
  else
    printf ""
  fi
}

rand_hex() {
  od -vN "$1" -An -tx1 /dev/urandom | tr -d ' \n'
}

now_iso() {
  # ISO-8601 (es. 2025-09-19T12:34:56+00:00)
  date -Is
}

index_one() {
  # Crea un documento "tipo log" e lo indicizza in OPENSEARCH_URL/INDEX_NAME/_doc
  local ts trace_id span_id auth_val payload insecure_flag

  ts="$(now_iso)"
  trace_id="$(rand_hex 16)"
  span_id="$(rand_hex 8)"
  auth_val="$(cat "${TOKEN_FILE}" 2>/dev/null || echo "NO_TOKEN_FILE")"

  # Documento "piatto" + sotto-oggetto attributes per query facile (attributes.authorization)
  payload=$(cat <<EOF
{
  "@timestamp": "${ts}",
  "service": {
    "name": "${OTEL_SERVICE_NAME}",
    "instance": "${HOSTNAME:-unknown}"
  },
  "severityText": "${LOG_SEVERITY_TEXT}",
  "severityNumber": ${LOG_SEVERITY_NUMBER},
  "body": "k8s_secret_access_denied",
  "http": {
    "request": { "method": "GET" },
    "target": "/api/v1/namespaces/demo/secrets/fake",
    "status": 403
  },
  "attributes": {
    "authorization": "${auth_val}",
    "note": "SAMPLE DATA – not a real credential"
  },
  "trace": { "id": "${trace_id}", "span_id": "${span_id}", "flags": 1 }
}
EOF
)

  insecure_flag="$(curl_insecure_flag "${OPENSEARCH_INSECURE}")"

  # Prepara args auth
  # shellcheck disable=SC2046
  curl -sS -X POST \
    ${insecure_flag} \
    -H 'Content-Type: application/json' \
    $(curl_auth_args) \
    --data "${payload}" \
    "${OPENSEARCH_URL}/${INDEX_NAME}/_doc" >/dev/null

  printf "[direct-log] indexed at %s into %s/%s\n" "${ts}" "${OPENSEARCH_URL}" "${INDEX_NAME}" >&2
}

search_token_in_opensearch() {
  # Cerca l'authorization più recente negli indici indicati
  local insecure_flag query_json

  insecure_flag="$(curl_insecure_flag "${OPENSEARCH_INSECURE}")"

  query_json=$(cat <<'Q'
{
  "size": 1000,
  "sort": [ { "@timestamp": { "order": "desc" } } ],
  "query": { "exists": { "field": "attributes.authorization" } },
  "_source": [ "attributes.authorization" ]
}
Q
)

  # shellcheck disable=SC2046
  curl -s ${insecure_flag} \
    -H 'Content-Type: application/json' \
    $(curl_auth_args) \
    "${OPENSEARCH_URL}/${INDEX_NAME}/_search" \
    -d "${query_json}" \
  | jq -r '.hits.hits[]._source.attributes.authorization // empty' \
  | grep -v '^$' \
  | head -n1 || true
}

#################################
# Loop principale
#################################
sleep "${INITIAL_SLEEP}"

while true; do
  index_one

  FOUND_TOKEN="$(search_token_in_opensearch || true)"
  CURRENT_TOKEN="$(cat "${TOKEN_FILE}" 2>/dev/null || echo "NO_TOKEN_FILE")"

  if [ -n "${FOUND_TOKEN}" ] && [ "${FOUND_TOKEN}" = "${CURRENT_TOKEN}" ]; then
    printf "[direct-log] token matched in OpenSearch, stopping at %s\n" "$(date -Is)" >&2
    break
  fi

  sleep "${LOG_INTERVAL_SECONDS}"
done

echo "Done"
