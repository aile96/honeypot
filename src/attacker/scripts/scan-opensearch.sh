#!/usr/bin/env sh
set -eu

OS_ENDPOINT="${OS_ENDPOINT:-http://opensearch.mem:9200}"
OUT="${OUT:-/token}"
MAX=1000
TOKEN=""

: > "$OUT"

for i in $(seq 1 $MAX); do
  TOKEN="$(curl -s "${OS_ENDPOINT}/_all/_search" \
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
    echo "Trovato al tentativo $i:"
    echo "$TOKEN"
    break
  fi

  sleep 1
done

if [ -z "$TOKEN" ]; then
  echo "Nessun valore trovato dopo $MAX tentativi"
fi

echo "TOKEN=$TOKEN"
echo "$TOKEN" > "$OUT"

LINES="$(wc -l < "$OUT" | tr -d ' ')"
echo "Salvati ${LINES} valori di authorization fittizi in: $OUT"
