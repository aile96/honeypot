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

echo "TOKEN: $TOKEN"

# Curl options
CURL_OPTS=(--silent --show-error --fail --retry 3 --retry-delay 1
  -H "Authorization: Bearer ${TOKEN}"
  -H "Accept: application/json"
)

if [[ -f "$CA_CERT" ]]; then
  CURL_OPTS+=(--cacert "$CA_CERT")
else
  echo "NOTE: CA ${CA_CERT} not found. Proceds with --insecure." >&2
  CURL_OPTS+=(--insecure)
fi

echo "[*] List of all CiliumNetworkPolicies to remove..."
LIST_URL="${APISERVER}/apis/cilium.io/v2/ciliumnetworkpolicies"

# Extract (namespace, name) of every CNP
mapfile -t CNP_ITEMS < <(
  curl "${CURL_OPTS[@]}" "$LIST_URL" \
    | jq -r '.items[] | "\(.metadata.namespace) \(.metadata.name)"'
)

if [[ "${#CNP_ITEMS[@]}" -eq 0 ]]; then
  echo "No CiliumNetworkPolicy found. Nothing to do"
  exit 0
fi

echo "Found ${#CNP_ITEMS[@]} CiliumNetworkPolicies. Removing..."

# Removing all
for line in "${CNP_ITEMS[@]}"; do
  ns="$(awk '{print $1}' <<<"$line")"
  name="$(awk '{print $2}' <<<"$line")"
  DEL_URL="${APISERVER}/apis/cilium.io/v2/namespaces/${ns}/ciliumnetworkpolicies/${name}"

  echo -n " - Deleting ${ns}/${name} ... "
  if curl "${CURL_OPTS[@]}" -X DELETE "$DEL_URL" >/dev/null; then
    echo "OK"
  else
    echo "FAILED" >&2
  fi
done

echo "Done"
