#!/usr/bin/env bash
set -euo pipefail

APISERVER=$1

# Trying to guess API server from environment of pod/node
if [[ -z "${APISERVER}" ]]; then
  if [[ -n "${KUBERNETES_SERVICE_HOST:-}" && -n "${KUBERNETES_SERVICE_PORT:-}" ]]; then
    APISERVER="https://${KUBERNETES_SERVICE_HOST}:${KUBERNETES_SERVICE_PORT}"
  else
    APISERVER="https://kubernetes.default.svc"
  fi
fi

CA_CERT="${CA_CERT:-/var/run/secrets/kubernetes.io/serviceaccount/ca.crt}"

if ! command -v jq >/dev/null 2>&1; then
  echo "ERROR: 'jq' not found. Install jq to continue" >&2
  exit 1
fi

# Extract token
TOKEN="$(cat /tmp/token)"

CURL_OPTS=(--silent --show-error --fail --retry 3 --retry-delay 1
  -H "Authorization: Bearer ${TOKEN}"
  -H "Accept: application/json"
)
if [[ -f "$CA_CERT" ]]; then
  CURL_OPTS+=(--cacert "$CA_CERT")
else
  echo "NOTE: CA ${CA_CERT} not found. Proceeding with --insecure." >&2
  CURL_OPTS+=(--insecure)
fi

API_GROUP="networking.k8s.io"
API_VERSION="v1"
RESOURCE="networkpolicies"

echo "[*] Listing ALL NetworkPolicies cluster-wide..."
LIST_URL_BASE="${APISERVER}/apis/${API_GROUP}/${API_VERSION}/${RESOURCE}"

# Collect (namespace name) pairs with pagination support
declare -a NP_ITEMS=()
CONT_TOKEN=""
while :; do
  QS="limit=500"
  [[ -n "$CONT_TOKEN" ]] && QS="${QS}&continue=$(python3 - <<PY
import urllib.parse, os
print(urllib.parse.quote(os.environ["CONT_TOKEN"]))
PY
)"
  RESP="$(curl "${CURL_OPTS[@]}" "${LIST_URL_BASE}?${QS}")"
  mapfile -t CHUNK < <(jq -r '.items[] | "\(.metadata.namespace) \(.metadata.name)"' <<<"$RESP")
  NP_ITEMS+=("${CHUNK[@]}")
  CONT_TOKEN="$(jq -r '.metadata.continue // ""' <<<"$RESP")"
  [[ -z "$CONT_TOKEN" ]] && break
done

if [[ "${#NP_ITEMS[@]}" -eq 0 ]]; then
  echo "No NetworkPolicies found. Nothing to do."
  exit 0
fi

echo "Found ${#NP_ITEMS[@]} NetworkPolicies. Removing..."
for line in "${NP_ITEMS[@]}"; do
  ns="$(awk '{print $1}' <<<"$line")"
  name="$(awk '{print $2}' <<<"$line")"
  DEL_URL="${APISERVER}/apis/${API_GROUP}/${API_VERSION}/namespaces/${ns}/${RESOURCE}/${name}"

  echo -n " - Deleting ${ns}/${name} ... "
  if curl "${CURL_OPTS[@]}" -X DELETE "$DEL_URL" >/dev/null; then
    echo "OK"
  else
    echo "FAILED" >&2
  fi
done

echo "Done."
