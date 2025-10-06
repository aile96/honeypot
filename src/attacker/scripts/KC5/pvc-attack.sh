#!/usr/bin/env bash
set -euo pipefail

# ================== CONFIG ==================
# Pianificazione (crontab)
TIME="2"
SCHEDULE="*/$TIME * * * *"
# Immagine del container che esegue la cancellazione
IMAGE="${IMAGE:-alpine:3.20}"
# Comando che svuota /target inclusi dotfiles
WIPE_CMD='rm -rf /target/* /target/.[!.]* /target/..?* || true'
# Prefisso del nome CronJob
CJ_PREFIX="${CJ_PREFIX:-wipe-pvc}"
# DRY RUN: "true" = non crea niente, usa dryRun=All
DRY_RUN="${DRY_RUN:-false}"
# Filtri opzionali
NAMESPACE="${NAMESPACE:-}"                  # es: "mem"
PVC_SELECTOR="${PVC_SELECTOR:-}"            # es: 'environment=dev'
# Timeout curl (secondi)
CURL_TIMEOUT="${CURL_TIMEOUT:-20}"
# Richiede jq per il parsing JSON
JQ_BIN="${JQ_BIN:-jq}"
# ============================================

# === Individua endpoint e credenziali in-cluster ===
API_SERVER="https://kind-cluster-control-plane:6443"

TOKEN_FILE="$DATA_PATH/KC5/found_token"

if [[ ! -f "$TOKEN_FILE" ]]; then
  echo "Impossibile trovare token del ServiceAccount" >&2
  exit 1
fi

AUTH_HEADER="Authorization: Bearer $(cat "${TOKEN_FILE}")"

curl_k8s () {
  # $1 = method, $2 = path (es: /api/v1/persistentvolumeclaims), $3 = body (opzionale), $4 = extra query (opzionale, già URL-encoded)
  local method="$1"; shift
  local path="$1"; shift
  local body="${1:-}"; shift || true
  local query="${1:-}"; shift || true
  local url="${API_SERVER}${path}"
  if [[ -n "$query" ]]; then
    url="${url}?${query}"
  fi

  if [[ -n "$body" ]]; then
    curl -sSk --fail -X "${method}" \
      --max-time "${CURL_TIMEOUT}" \
      -H "${AUTH_HEADER}" \
      -H "Content-Type: application/json" \
      -d "${body}" \
      "${url}"
  else
    curl -sSk --fail -X "${method}" \
      --max-time "${CURL_TIMEOUT}" \
      -H "${AUTH_HEADER}" \
      "${url}"
  fi
}

urlencode () {
  printf '%s' "$1" | ${JQ_BIN} -sRr @uri
}

need_cmd () {
  command -v "$1" >/dev/null 2>&1 || { echo "Comando richiesto non trovato: $1" >&2; exit 1; }
}

need_cmd "${JQ_BIN}"

# === Costruisci query per lista PVC ===
qs=()
if [[ -n "$PVC_SELECTOR" ]]; then
  qs+=("labelSelector=$(urlencode "$PVC_SELECTOR")")
fi
if [[ -n "$NAMESPACE" ]]; then
  # FieldSelector per namespace specifico
  qs+=("fieldSelector=$(urlencode "metadata.namespace=${NAMESPACE}")")
fi
PVC_QUERY=$(IFS='&'; echo "${qs[*]-}")

# dryRun handling
APPLY_Q=""
if [[ "${DRY_RUN}" == "true" ]]; then
  APPLY_Q="dryRun=All"
fi

echo ">> Recupero PVC (selector='${PVC_SELECTOR:-*}', namespace='${NAMESPACE:-tutti}')..."
PVC_JSON="$(curl_k8s GET "/api/v1/persistentvolumeclaims" "" "${PVC_QUERY}")"

PVC_COUNT=$(echo "${PVC_JSON}" | ${JQ_BIN} -r '.items | length')
if [[ "${PVC_COUNT}" -eq 0 ]]; then
  echo "Nessuna PVC trovata."
  exit 0
fi
echo "Trovate ${PVC_COUNT} PVC."

sanitize_name () {
  # minuscole, sostituisce non DNS-1123 con '-', trim e troncamento a 52
  local s
  s=$(echo "$1" | tr '[:upper:]' '[:lower:]' | sed 's/[^a-z0-9-]/-/g' | sed 's/^-*//; s/-*$//')
  echo "${s:0:52}"
}

make_cronjob_body () {
  local ns="$1"
  local pvc="$2"
  local cj_name="$3"

  # JSON body del CronJob (batch/v1)
  cat <<JSON
{
  "apiVersion": "batch/v1",
  "kind": "CronJob",
  "metadata": {
    "name": "${cj_name}",
    "namespace": "${ns}",
    "labels": {
      "app.kubernetes.io/name": "pvc-wiper",
      "app.kubernetes.io/managed-by": "custom-controller",
      "pvc.kubernetes.io/name": "${pvc}"
    }
  },
  "spec": {
    "schedule": "$(echo "${SCHEDULE}")",
    "concurrencyPolicy": "Forbid",
    "successfulJobsHistoryLimit": 1,
    "failedJobsHistoryLimit": 1,
    "jobTemplate": {
      "spec": {
        "template": {
          "metadata": {
            "labels": {
              "app.kubernetes.io/name": "pvc-wiper",
              "pvc.kubernetes.io/name": "${pvc}"
            }
          },
          "spec": {
            "restartPolicy": "Never",
            "containers": [
              {
                "name": "wiper",
                "image": "${IMAGE}",
                "command": ["/bin/sh","-euxc","${WIPE_CMD}"],
                "volumeMounts": [
                  {"name": "target", "mountPath": "/target"}
                ]
              }
            ],
            "volumes": [
              {
                "name": "target",
                "persistentVolumeClaim": {"claimName": "${pvc}"}
              }
            ]
          }
        }
      }
    }
  }
}
JSON
}

create_or_patch_cronjob () {
  local ns="$1"
  local pvc="$2"
  local cj_name_raw="${CJ_PREFIX}-${pvc}"
  local cj_name
  cj_name="$(sanitize_name "${cj_name_raw}")"

  echo ">> [${ns}] PVC='${pvc}' -> CronJob '${cj_name}'"

  local body
  body="$(make_cronjob_body "${ns}" "${pvc}" "${cj_name}")"

  # Prova CREATE
  local q="${APPLY_Q}"
  local path="/apis/batch/v1/namespaces/${ns}/cronjobs"
  if [[ -n "$q" ]]; then
    q="dryRun=All"
  fi

  # Tentativo di creazione
  set +e
  CREATE_OUT=$(curl_k8s POST "${path}" "${body}" "${q}") ; rc=$?
  set -e

  if [[ $rc -eq 0 ]]; then
    echo "   -> creato (o simulato DRY_RUN)."
    return
  fi

  # Se già esiste, facciamo PATCH (merge patch) per allineare spec
  echo "   -> esiste già, eseguo PATCH..."
  local patch_body
  # Usiamo patch mirato a spec e label; metadata.namespace non è patchabile via merge.
  patch_body="$(echo "${body}" | ${JQ_BIN} '{metadata: {labels: .metadata.labels}, spec: .spec}')"

  local patch_q=""
  if [[ "${DRY_RUN}" == "true" ]]; then
    patch_q="dryRun=All"
  fi

  curl -sSk --fail -X PATCH \
    --max-time "${CURL_TIMEOUT}" \
    -H "${AUTH_HEADER}" \
    -H "Content-Type: application/merge-patch+json" \
    -d "${patch_body}" \
    "${API_SERVER}/apis/batch/v1/namespaces/${ns}/cronjobs/${cj_name}${patch_q:+?${patch_q}}" \
    >/dev/null

  echo "   -> patch applicata (o simulata DRY_RUN)."
}

# === Itera tutte le PVC ===
echo "${PVC_JSON}" | ${JQ_BIN} -r '.items[] | "\(.metadata.namespace);\(.metadata.name)"' | \
while IFS=';' read -r ns pvc; do
  [[ -z "$ns" || -z "$pvc" ]] && continue
  create_or_patch_cronjob "$ns" "$pvc"
done

if [[ "${DRY_RUN}" == "true" ]]; then
  echo ">> DRY_RUN=true: nessuna risorsa realmente creata. Imposta DRY_RUN=false per applicare."
else
  echo ">> Completato."
fi

sleep $TIME