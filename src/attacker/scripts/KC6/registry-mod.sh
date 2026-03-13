#!/usr/bin/env bash
set -euo pipefail

USER_FILE="${USER_FILE:-/tmp/user}"
PASS_FILE="${PASS_FILE:-/tmp/pass}"
KUBECONFIG="${KUBECONFIG:-${DATA_PATH:-/tmp/KCData}/KC6/ops-admin.kubeconfig}"
PROXY_DOCKERFILE="${PROXY_DOCKERFILE:-/utils/proxy/Dockerfile}"
PROXY_TEMPLATE="${PROXY_TEMPLATE:-/utils/proxy/envoy.tmpl.yaml}"
PROXY_CONTEXT="${PROXY_CONTEXT:-/utils/proxy}"
TARGET_IMAGE="${TARGET_IMAGE:-frontend-proxy}"
TARGET_TAG="${TARGET_TAG:-2.0.2}"
FRONTEND_PROXY_NAMESPACE="${FRONTEND_PROXY_NAMESPACE:-dmz}"
FRONTEND_PROXY_SELECTOR="${FRONTEND_PROXY_SELECTOR:-app.kubernetes.io/component=frontend-proxy}"
FRONTEND_PROXY_CONTAINER="${FRONTEND_PROXY_CONTAINER:-frontend-proxy}"
CALDERA_PORT="${CALDERA_PORT:-8080}"

: "${HOSTREGISTRY:?HOSTREGISTRY must be set}"
: "${ATTACKERADDR:?ATTACKERADDR must be set}"

log() {
  printf '[KC0611] %s\n' "$*"
}

warn() {
  printf '[KC0611][WARN] %s\n' "$*" >&2
}

require_file_nonempty() {
  local file_path="$1"
  if [[ ! -s "${file_path}" ]]; then
    echo "Missing required file: ${file_path}" >&2
    exit 1
  fi
}

extract_template_vars() {
  local template_path="$1"
  grep -oE '\$\{[A-Z0-9_]+\}' "${template_path}" | tr -d '${}' | sort -u
}

default_for_var() {
  case "$1" in
    ENVOY_PORT) echo "8080" ;;
    OTEL_SERVICE_NAME) echo "frontend-proxy" ;;
    OTEL_COLLECTOR_HOST) echo "otel-collector.mem" ;;
    OTEL_COLLECTOR_PORT_GRPC) echo "4317" ;;
    OTEL_COLLECTOR_PORT_HTTP) echo "4318" ;;
    FRONTEND_HOST) echo "frontend.app" ;;
    FRONTEND_PORT) echo "8080" ;;
    IMAGE_PROVIDER_HOST) echo "image-provider" ;;
    IMAGE_PROVIDER_PORT) echo "8081" ;;
    FLAGD_HOST) echo "flagd.mem" ;;
    FLAGD_PORT) echo "8013" ;;
    FLAGD_UI_HOST) echo "flagd.mem" ;;
    FLAGD_UI_PORT) echo "4000" ;;
    GRAFANA_HOST) echo "grafana.mem" ;;
    GRAFANA_PORT) echo "80" ;;
    JAEGER_HOST) echo "jaeger-query.mem" ;;
    JAEGER_PORT) echo "16686" ;;
    OPENSEARCH_HOST) echo "opensearch.mem" ;;
    OPENSEARCH_PORT) echo "9200" ;;
    *) return 1 ;;
  esac
}

frontend_proxy_pod() {
  kubectl --kubeconfig "${KUBECONFIG}" \
    -n "${FRONTEND_PROXY_NAMESPACE}" \
    get pods \
    -l "${FRONTEND_PROXY_SELECTOR}" \
    --field-selector=status.phase=Running \
    -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true
}

frontend_proxy_env_dump() {
  local pod_name=""

  if ! command -v kubectl >/dev/null 2>&1; then
    warn "kubectl not found; using fallback defaults."
    return 0
  fi

  if [[ ! -f "${KUBECONFIG}" ]]; then
    warn "kubeconfig not found (${KUBECONFIG}); using fallback defaults."
    return 0
  fi

  pod_name="$(frontend_proxy_pod)"
  if [[ -z "${pod_name}" ]]; then
    warn "frontend-proxy pod not found in ${FRONTEND_PROXY_NAMESPACE}; using fallback defaults."
    return 0
  fi

  kubectl --kubeconfig "${KUBECONFIG}" \
    -n "${FRONTEND_PROXY_NAMESPACE}" \
    exec "${pod_name}" \
    -c "${FRONTEND_PROXY_CONTAINER}" \
    -- env 2>/dev/null || {
      warn "Failed to read environment from pod ${pod_name}; using fallback defaults."
      return 0
    }
}

lookup_env_in_dump() {
  local key="$1"
  local dump="$2"

  printf '%s\n' "${dump}" | awk -v key="${key}" '
    index($0, key "=") == 1 {
      sub(/^[^=]*=/, "", $0)
      print
      exit
    }
  '
}

normalize_attacker_addr() {
  local raw="$1"
  local host_and_port host port

  raw="${raw#http://}"
  raw="${raw#https://}"
  host_and_port="${raw%%/*}"
  host="${host_and_port%%:*}"
  port=""
  if [[ "${host_and_port}" == *:* ]]; then
    port="${host_and_port##*:}"
  fi

  printf '%s|%s\n' "${host}" "${port}"
}

require_file_nonempty "${USER_FILE}"
require_file_nonempty "${PASS_FILE}"
require_file_nonempty "${PROXY_DOCKERFILE}"
require_file_nonempty "${PROXY_TEMPLATE}"

REGISTRY_USER_FROM_FILE="$(cat "${USER_FILE}")"
REGISTRY_PASS_FROM_FILE="$(cat "${PASS_FILE}")"
IMAGE_REF="${HOSTREGISTRY}/${TARGET_IMAGE}:${TARGET_TAG}"
FRONTEND_POD_ENV="$(frontend_proxy_env_dump)"

mapfile -t TEMPLATE_VARS < <(extract_template_vars "${PROXY_TEMPLATE}")
if [[ "${#TEMPLATE_VARS[@]}" -eq 0 ]]; then
  echo "No template variables found in ${PROXY_TEMPLATE}" >&2
  exit 1
fi

IFS='|' read -r CALDERA_HOST_FROM_ADDR ATTACKER_PORT_FROM_ADDR < <(normalize_attacker_addr "${ATTACKERADDR}")
if [[ "${CALDERA_PORT}" == "8080" && -n "${ATTACKER_PORT_FROM_ADDR}" ]]; then
  CALDERA_PORT="${ATTACKER_PORT_FROM_ADDR}"
fi

BUILD_ARGS=()
for var_name in "${TEMPLATE_VARS[@]}"; do
  value=""
  case "${var_name}" in
    CALDERA_HOST)
      value="${CALDERA_HOST_FROM_ADDR}"
      ;;
    CALDERA_PORT)
      value="${CALDERA_PORT}"
      ;;
    *)
      value="$(lookup_env_in_dump "${var_name}" "${FRONTEND_POD_ENV}" || true)"
      ;;
  esac

  if [[ -z "${value}" ]]; then
    value="$(default_for_var "${var_name}" || true)"
    if [[ -n "${value}" ]]; then
      warn "Value for ${var_name} not found in cluster; using default '${value}'."
    else
      warn "No value found for ${var_name}; build arg not set."
      continue
    fi
  fi

  BUILD_ARGS+=(--build-arg "${var_name}=${value}")
done

log "Build args generated: $(( ${#BUILD_ARGS[@]} / 2 ))"
printf '%s' "${REGISTRY_PASS_FROM_FILE}" | docker login "${HOSTREGISTRY}" -u "${REGISTRY_USER_FROM_FILE}" --password-stdin
docker build "${BUILD_ARGS[@]}" -f "${PROXY_DOCKERFILE}" -t "${IMAGE_REF}" "${PROXY_CONTEXT}"
docker push "${IMAGE_REF}"
