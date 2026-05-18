#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMMON_LIB="${PROJECT_ROOT}/src/lab/lib-bash/common.sh"

if [[ ! -f "${COMMON_LIB}" ]]; then
  printf "[ERROR] Common library not found: %s\n" "${COMMON_LIB}" >&2
  exit 1
fi
source "${COMMON_LIB}"
log "Common library loaded from ${COMMON_LIB}."

ENV_FILE="${PROJECT_ROOT}/configuration.conf"
if [[ -f "${ENV_FILE}" ]]; then
  load_env_file "$ENV_FILE" || err "Failed to load configuration from: $ENV_FILE"
fi

LAB_NAME="${1:-${LAB_NAME:-${CLUSTER_PROFILE:-honeypotlab}}}"
CLUSTER_TARGET="${CLUSTER_TARGET:-5Gcore}"
HOST_RES_DIR="${PROJECT_ROOT}/res"
HOST_RUNTIME_DIR="${HOST_RES_DIR}/runtime/${LAB_NAME}"
INFO_FILE="${HOST_RUNTIME_DIR}/info"
CONTROLLER_CONTAINER_NAME="${CONTROLLER_CONTAINER_NAME:-${LAB_NAME}-controller}"
COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-honeypot-${LAB_NAME}}"
CP_NETWORK="${CP_NETWORK:-kind-${LAB_NAME}}"
KIND_CLUSTER="${LAB_NAME}"

[[ "${LAB_NAME}" =~ ^[A-Za-z0-9][A-Za-z0-9_.-]*$ ]] || err "Invalid LAB_NAME='${LAB_NAME}'."
[[ "${CONTROLLER_CONTAINER_NAME}" =~ ^[A-Za-z0-9][A-Za-z0-9_.-]*$ ]] || err "Invalid CONTROLLER_CONTAINER_NAME='${CONTROLLER_CONTAINER_NAME}'."

req docker
docker ps >/dev/null 2>&1 || err "Docker daemon is not reachable. Start Docker and retry."

json_get() {
  local file="$1" key="$2"
  python3 - "$file" "$key" <<'PY_JSON_GET' 2>/dev/null || true
import json, sys
try:
    data = json.load(open(sys.argv[1], encoding='utf-8'))
    value = data.get(sys.argv[2], '')
    print(value if value is not None else '')
except Exception:
    pass
PY_JSON_GET
}

if [[ -f "${INFO_FILE}" ]]; then
  CONTROLLER_CONTAINER_NAME="$(json_get "${INFO_FILE}" controller_container || true)"
  COMPOSE_PROJECT_NAME="$(json_get "${INFO_FILE}" compose_project || true)"
  CP_NETWORK="$(json_get "${INFO_FILE}" docker_network || true)"
  KIND_CLUSTER="$(json_get "${INFO_FILE}" kind_cluster || true)"
  CLUSTER_TARGET="$(json_get "${INFO_FILE}" cluster_target || true)"
  CLUSTER_TARGET="${CLUSTER_TARGET:-5Gcore}"
  CONTROLLER_CONTAINER_NAME="${CONTROLLER_CONTAINER_NAME:-${LAB_NAME}-controller}"
  COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-honeypot-${LAB_NAME}}"
  CP_NETWORK="${CP_NETWORK:-kind-${LAB_NAME}}"
  KIND_CLUSTER="${KIND_CLUSTER:-${LAB_NAME}}"
fi

signal_controller() {
  if ! docker ps --format '{{.Names}}' | grep -qx "${CONTROLLER_CONTAINER_NAME}"; then
    return 0
  fi
  log "Stopping entrypoint.py inside '${CONTROLLER_CONTAINER_NAME}'."
  docker exec "${CONTROLLER_CONTAINER_NAME}" sh -lc \
    "pid=\"\$(ps -eo pid=,args= | awk '/entrypoint[.]py/ {print \$1; exit}')\"; if [ -n \"\$pid\" ]; then kill -TERM \"\$pid\"; fi" \
    >/dev/null 2>&1 || warn "Could not signal entrypoint.py inside '${CONTROLLER_CONTAINER_NAME}'."
  sleep 3
}

remove_controller() {
  if docker ps -a --format '{{.Names}}' | grep -qx "${CONTROLLER_CONTAINER_NAME}"; then
    log "Removing controller container '${CONTROLLER_CONTAINER_NAME}'."
    docker rm -f "${CONTROLLER_CONTAINER_NAME}" >/dev/null 2>&1 || warn "Could not remove controller container '${CONTROLLER_CONTAINER_NAME}'."
  else
    warn "No controller container named '${CONTROLLER_CONTAINER_NAME}' found."
  fi
}

cleanup_compose() {
  if docker compose version >/dev/null 2>&1; then
    local compose_file="${PROJECT_ROOT}/src/${CLUSTER_TARGET}/conf-files/compose.yaml"
    if [[ -f "${compose_file}" ]]; then
      log "Removing Compose project '${COMPOSE_PROJECT_NAME}'."
      docker compose -f "${compose_file}" -p "${COMPOSE_PROJECT_NAME}" down --remove-orphans -v >/dev/null 2>&1 || warn "Compose cleanup failed for project '${COMPOSE_PROJECT_NAME}'."
    fi
  fi
}

cleanup_kind() {
  if command -v kind >/dev/null 2>&1 && kind get clusters 2>/dev/null | grep -qx "${KIND_CLUSTER}"; then
    log "Deleting Kind cluster '${KIND_CLUSTER}'."
    kind delete cluster --name "${KIND_CLUSTER}" >/dev/null 2>&1 || warn "Kind cleanup failed for '${KIND_CLUSTER}'."
  fi
}

cleanup_labeled_containers() {
  local ids
  ids="$(docker ps -a --filter "label=honeypot.lab=${LAB_NAME}" -q || true)"
  if [[ -n "${ids}" ]]; then
    log "Removing Docker containers labelled honeypot.lab=${LAB_NAME}."
    docker rm -f ${ids} >/dev/null 2>&1 || warn "Could not remove all labelled containers for '${LAB_NAME}'."
  fi
}

cleanup_network() {
  if docker network inspect "${CP_NETWORK}" >/dev/null 2>&1; then
    log "Removing Docker network '${CP_NETWORK}'."
    docker network rm "${CP_NETWORK}" >/dev/null 2>&1 || warn "Could not remove Docker network '${CP_NETWORK}'."
  fi
}

cleanup_runtime_dir() {
  if [[ ! -d "${HOST_RUNTIME_DIR}" ]]; then
    return 0
  fi

  log "Removing runtime directory '${HOST_RUNTIME_DIR}'."
  if rm -rf "${HOST_RUNTIME_DIR}" 2>/dev/null; then
    return 0
  fi

  warn "Regular runtime cleanup failed; retrying through a helper container."
  docker run --rm \
    -v "${HOST_RES_DIR}/runtime:/runtime" \
    alpine:3.20 \
    rm -rf "/runtime/${LAB_NAME}" >/dev/null 2>&1 || warn "Helper container runtime cleanup failed for '${LAB_NAME}'."
}

verify_cleanup() {
  local failed=0 leftovers
  leftovers="$(docker ps -a --filter "label=honeypot.lab=${LAB_NAME}" -q || true)"
  if [[ -n "${leftovers}" ]]; then
    warn "Docker containers still exist for lab '${LAB_NAME}'."
    failed=1
  fi
  if command -v kind >/dev/null 2>&1 && kind get clusters 2>/dev/null | grep -qx "${KIND_CLUSTER}"; then
    warn "Kind cluster '${KIND_CLUSTER}' still exists."
    failed=1
  fi
  if docker network inspect "${CP_NETWORK}" >/dev/null 2>&1; then
    warn "Docker network '${CP_NETWORK}' still exists."
    failed=1
  fi
  if [[ -d "${HOST_RUNTIME_DIR}" ]]; then
    warn "Runtime directory '${HOST_RUNTIME_DIR}' still exists."
    failed=1
  fi
  (( failed == 0 )) || err "Cleanup incomplete for lab '${LAB_NAME}'."
}

signal_controller
remove_controller
cleanup_compose
cleanup_kind
cleanup_labeled_containers
cleanup_network
cleanup_runtime_dir
verify_cleanup
log "Cleanup completed for lab '${LAB_NAME}'."
