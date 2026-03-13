#!/usr/bin/env bash
set -eu

LOG_ENDPOINT="http://opensearch.$LOG_NS:9200"
LOG_INDEX="${LOG_INDEX:-logs-otel*}"
OUT_FILE="$DATA_PATH/KC1/token"
MAX=100
TOKEN=""

# Installing dependencies and setup
apt-get update >/dev/null 2>&1
apt-get install -y --no-install-recommends curl jq ca-certificates >/dev/null 2>&1
mkdir -p "$(dirname "$OUT_FILE")"

for i in $(seq 1 "$MAX"); do
  TOKEN="$(curl -s "${LOG_ENDPOINT}/${LOG_INDEX}/_search" \
    -H 'Content-Type: application/json' \
    -d '{
      "size": 1,
      "sort": [
        { "@timestamp": { "order": "desc" } }
      ],
      "query": {
        "bool": {
          "filter": [
            { "exists": { "field": "attributes.authorization" } },
            { "term": { "http.status": 403 } }
          ],
          "must": [
            { "match_phrase": { "service.name": "traffic-controller" } },
            { "match_phrase": { "body": "k8s_secret_access_denied" } },
            { "match_phrase": { "attributes.note": "SAMPLE DATA" } }
          ]
        }
      },
      "_source": [ "attributes.authorization" ]
    }' \
    | jq -r '.hits.hits[0]._source.attributes.authorization // empty')"

  if [ -n "$TOKEN" ]; then
    echo "Found in attempt $i:"
    echo "$TOKEN"
    break
  fi

  sleep 5
done

if [ -z "$TOKEN" ]; then
  echo "No values after $MAX retries"
fi

echo "TOKEN=$TOKEN"
echo "$TOKEN" > "$OUT_FILE"
echo "Saved value in: $OUT_FILE"
