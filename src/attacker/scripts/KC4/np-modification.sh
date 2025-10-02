#!/usr/bin/env bash
set -euo pipefail

APISERVER=$1

# Prova a dedurre l'API server dall'ambiente del pod/nodo
if [[ -z "${APISERVER}" ]]; then
  if [[ -n "${KUBERNETES_SERVICE_HOST:-}" && -n "${KUBERNETES_SERVICE_PORT:-}" ]]; then
    APISERVER="https://${KUBERNETES_SERVICE_HOST}:${KUBERNETES_SERVICE_PORT}"
  else
    APISERVER="https://kubernetes.default.svc"
  fi
fi

CA_CERT="${CA_CERT:-/var/run/secrets/kubernetes.io/serviceaccount/ca.crt}"

if ! command -v jq >/dev/null 2>&1; then
  echo "ERRORE: 'jq' non trovato. Installa jq per continuare." >&2
  exit 1
fi

# Estrai token
TOKEN="$(cat /tmp/token)"

echo "TOKEN: $TOKEN"

# Opzioni curl: preferisci validare il certificato se disponibile
CURL_OPTS=(--silent --show-error --fail --retry 3 --retry-delay 1
  -H "Authorization: Bearer ${TOKEN}"
  -H "Accept: application/json"
)

if [[ -f "$CA_CERT" ]]; then
  CURL_OPTS+=(--cacert "$CA_CERT")
else
  echo "AVVISO: CA ${CA_CERT} non trovata. Procedo con --insecure." >&2
  CURL_OPTS+=(--insecure)
fi

echo "[*] Elenco di tutte le CiliumNetworkPolicies da rimuovere..."
LIST_URL="${APISERVER}/apis/cilium.io/v2/ciliumnetworkpolicies"

# Estrai (namespace, name) di ogni CNP
mapfile -t CNP_ITEMS < <(
  curl "${CURL_OPTS[@]}" "$LIST_URL" \
    | jq -r '.items[] | "\(.metadata.namespace) \(.metadata.name)"'
)

if [[ "${#CNP_ITEMS[@]}" -eq 0 ]]; then
  echo "Nessuna CiliumNetworkPolicy trovata. Nulla da fare."
  exit 0
fi

echo "Trovate ${#CNP_ITEMS[@]} CiliumNetworkPolicies. Procedo con la cancellazione..."

# Cancella ognuna
for line in "${CNP_ITEMS[@]}"; do
  ns="$(awk '{print $1}' <<<"$line")"
  name="$(awk '{print $2}' <<<"$line")"
  DEL_URL="${APISERVER}/apis/cilium.io/v2/namespaces/${ns}/ciliumnetworkpolicies/${name}"

  echo -n " - Deleting ${ns}/${name} ... "
  if curl "${CURL_OPTS[@]}" -X DELETE "$DEL_URL" >/dev/null; then
    echo "OK"
  else
    echo "FALLITA" >&2
  fi
done

echo "Completato."
