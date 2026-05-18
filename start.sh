#!/usr/bin/env bash
# Exit immediately on errors, undefined variables, or failed pipeline commands.
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
if ! load_env_file "$ENV_FILE"; then
  err "Failed to load configuration from: $ENV_FILE"
fi
log "Configuration loaded from ${ENV_FILE}."

CLUSTER_TARGET="${CLUSTER_TARGET:-5Gcore}"
LAB_NAME="${LAB_NAME:-${CLUSTER_PROFILE:-honeypotlab}}"
FOLLOW_CONTROLLER_LOGS="${FOLLOW_CONTROLLER_LOGS:-true}"
SKIP_RESOURCE_CHECK="${SKIP_RESOURCE_CHECK:-true}"
BUILD_CONTROLLER="${BUILD_CONTROLLER:-false}"
PROXY_BIND_ALL="${PROXY_BIND_ALL:-false}"
EXPOSE_TO_HOST="${EXPOSE_TO_HOST:-true}"
HOST_SOCKET="${HOST_SOCKET:-false}"
CONTROLLER_PROXY_CONTAINER_PORT="${CONTROLLER_PROXY_CONTAINER_PORT:-18080}"

CONTROLLER_CODE_DIR="/workdir/code"
CONTROLLER_COMMON_CODE_DIR="/workdir/common"
LAB_CONTROLLER_DIR="${PROJECT_ROOT}/src/lab/lab-controller"
CONTROLLER_ENV_FILE="${CONTROLLER_CODE_DIR}/conf-files/variables.py"
RES_DIR="/res"
HOST_RES_DIR="${PROJECT_ROOT}/${RES_DIR#/}"
HOST_CODE_ROOT="${PROJECT_ROOT}/src/${CLUSTER_TARGET}"
HOST_COMMON_CODE_DIR="${PROJECT_ROOT}/src/common"
HOST_RUNTIME_ROOT="${HOST_RES_DIR}/runtime"
HOST_RESULTS_ROOT="${HOST_RES_DIR}/results"
HOST_RUNTIME_DIR="${HOST_RUNTIME_ROOT}/${LAB_NAME}"
HOST_CONTROLLER_RESULTS_DIR="${HOST_RESULTS_ROOT}/${LAB_NAME}"
HOST_CONTROLLER_DOCKER_DATA_DIR="${HOST_RES_DIR}/cache/images/${LAB_NAME}"
HOST_BUILD_HELPER_CACHE_DIR="${HOST_RES_DIR}/cache/build-helper/${LAB_NAME}"
CONTROLLER_DOCKER_DATA_ROOT="${RES_DIR}/cache/images/${LAB_NAME}"
CONTROLLER_BUILD_HELPER_CACHE_DIR="${RES_DIR}/cache/build-helper/${LAB_NAME}"
RUNTIME_DIR="${RES_DIR}/runtime/${LAB_NAME}"
RESULTS_DIR="${RES_DIR}/results/${LAB_NAME}"
GENERATED_DIR="${RUNTIME_DIR}/generated"
STATE_FILE="${GENERATED_DIR}/lab-state.json"
CONTROLLER_CONTAINER_NAME="${CONTROLLER_CONTAINER_NAME:-${LAB_NAME}-controller}"
CP_NETWORK="${CP_NETWORK:-kind-${LAB_NAME}}"
COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-honeypot-${LAB_NAME}}"
CONTROLLER_IMAGE="${CONTROLLER_IMAGE:-lab-controller:latest}"
REQUIRED_AVAIL_MEM_MB="${REQUIRED_AVAIL_MEM_MB:-16384}"
REQUIRED_CPUS="${REQUIRED_CPUS:-6}"

[[ "${LAB_NAME}" =~ ^[A-Za-z0-9][A-Za-z0-9_.-]*$ ]] || err "Invalid LAB_NAME='${LAB_NAME}' for Docker resources."
[[ "${CONTROLLER_CONTAINER_NAME}" =~ ^[A-Za-z0-9][A-Za-z0-9_.-]*$ ]] || err "Invalid CONTROLLER_CONTAINER_NAME='${CONTROLLER_CONTAINER_NAME}'."
[[ "${CLUSTER_TARGET}" =~ ^[A-Za-z0-9._-]+$ ]] || err "Invalid CLUSTER_TARGET='${CLUSTER_TARGET}'."

TARGET_CONFIG_FILE="${PROJECT_ROOT}/src/${CLUSTER_TARGET}/conf-files/variables.py"
TARGET_COMPOSE_FILE="${PROJECT_ROOT}/src/${CLUSTER_TARGET}/conf-files/compose.yaml"
TARGET_KIND_TEMPLATE="${PROJECT_ROOT}/src/${CLUSTER_TARGET}/conf-files/kind-cluster.yaml.tmpl"
TARGET_SKAFFOLD_TEMPLATE="${PROJECT_ROOT}/src/${CLUSTER_TARGET}/conf-files/skaffold.yaml.tmpl"

[[ -f "${TARGET_CONFIG_FILE}" ]] || err "Target configuration file not found: ${TARGET_CONFIG_FILE}"
[[ -f "${TARGET_COMPOSE_FILE}" ]] || err "Target compose file not found: ${TARGET_COMPOSE_FILE}"
[[ -f "${TARGET_KIND_TEMPLATE}" ]] || err "Target kind template not found: ${TARGET_KIND_TEMPLATE}"
[[ -f "${TARGET_SKAFFOLD_TEMPLATE}" ]] || err "Target skaffold template not found: ${TARGET_SKAFFOLD_TEMPLATE}"
[[ -d "${PROJECT_ROOT}/src/${CLUSTER_TARGET}/containers" ]] || err "Expected containers directory not found for target '${CLUSTER_TARGET}'."
[[ -d "${PROJECT_ROOT}/src/${CLUSTER_TARGET}/helm-charts" ]] || err "Expected helm-charts directory not found for target '${CLUSTER_TARGET}'."
[[ -d "${HOST_COMMON_CODE_DIR}" ]] || err "Expected common directory not found at ${HOST_COMMON_CODE_DIR}"

normalize_bool_var SKIP_RESOURCE_CHECK
normalize_bool_var FOLLOW_CONTROLLER_LOGS
normalize_bool_var EXPOSE_TO_HOST
normalize_bool_var BUILD_CONTROLLER
normalize_bool_var PROXY_BIND_ALL
normalize_bool_var HOST_SOCKET
require_port_var CONTROLLER_PROXY_CONTAINER_PORT

log "Configuration validated. LAB_NAME=${LAB_NAME}, CLUSTER_TARGET=${CLUSTER_TARGET}, HOST_SOCKET=${HOST_SOCKET}, EXPOSE_TO_HOST=${EXPOSE_TO_HOST}, PROXY_BIND_ALL=${PROXY_BIND_ALL}."

get_mem_available_mb() {
  if [[ -r /proc/meminfo ]]; then
    awk '/MemAvailable:/ {print int($2/1024)}' /proc/meminfo
    return 0
  fi
  if command -v vm_stat >/dev/null 2>&1; then
    local pagesize free inactive speculative
    pagesize="$(vm_stat | awk '/page size of/ {gsub(/[^0-9]/, "", $8); print $8}')"
    free="$(vm_stat | awk '/Pages free/ {gsub(/[^0-9]/, "", $3); print $3}')"
    inactive="$(vm_stat | awk '/Pages inactive/ {gsub(/[^0-9]/, "", $3); print $3}')"
    speculative="$(vm_stat | awk '/Pages speculative/ {gsub(/[^0-9]/, "", $3); print $3}')"
    speculative="${speculative:-0}"
    if [[ -n "$pagesize" && -n "$free" && -n "$inactive" ]]; then
      echo $(( (free + inactive + speculative) * pagesize / 1024 / 1024 ))
      return 0
    fi
  fi
  return 1
}

get_cpu_count() {
  if command -v nproc >/dev/null 2>&1; then nproc; return 0; fi
  if command -v sysctl >/dev/null 2>&1; then sysctl -n hw.ncpu; return 0; fi
  return 1
}

check_system_resources() {
  local avail_mem_mb cpu_count
  avail_mem_mb="$(get_mem_available_mb || true)"
  cpu_count="$(get_cpu_count || true)"
  [[ -n "${avail_mem_mb}" && "${avail_mem_mb}" -gt 0 ]] || err "Unable to determine available RAM. Need at least ${REQUIRED_AVAIL_MEM_MB} MB available."
  (( avail_mem_mb >= REQUIRED_AVAIL_MEM_MB )) || err "Not enough available RAM: ${avail_mem_mb} MB available, need at least ${REQUIRED_AVAIL_MEM_MB} MB."
  [[ -n "${cpu_count}" && "${cpu_count}" -gt 0 ]] || err "Unable to determine CPU count. Need at least ${REQUIRED_CPUS} CPUs."
  (( cpu_count >= REQUIRED_CPUS )) || err "Not enough CPU cores: ${cpu_count} available, need at least ${REQUIRED_CPUS}."
}

req docker
docker ps >/dev/null 2>&1 || err "Docker daemon is not reachable. Start Docker and retry."

if is_false "${SKIP_RESOURCE_CHECK}"; then
  log "Checking system resources before starting the lab..."
  check_system_resources
  log "System resource check passed."
else
  log "Skipping system resource check as per configuration."
fi

json_get() {
  local file="$1" key="$2"
  python3 - "$file" "$key" <<'PY_JSON_GET' 2>/dev/null || true
import json, sys
try:
    data = json.load(open(sys.argv[1], encoding='utf-8'))
    value = data.get(sys.argv[2], '')
    if isinstance(value, bool):
        print('true' if value else 'false')
    else:
        print(value)
except Exception:
    pass
PY_JSON_GET
}

check_host_socket_conflicts() {
  is_true "${HOST_SOCKET}" || return 0
  local info lab controller host_socket
  shopt -s nullglob
  for info in "${HOST_RUNTIME_ROOT}"/*/info; do
    [[ "${info}" == "${HOST_RUNTIME_DIR}/info" ]] && continue
    host_socket="$(json_get "${info}" host_socket)"
    [[ "${host_socket}" == "true" ]] || continue
    lab="$(json_get "${info}" lab_name)"
    controller="$(json_get "${info}" controller_container)"
    err "Another lab is already using the host Docker socket or left stale runtime state: ${lab:-${info}}. Run './remove_all.sh ${lab:-}' before starting this lab."
  done
  shopt -u nullglob
}

write_info_file() {
  local status="$1" host_port="${2:-}"
  mkdir -p "${HOST_RUNTIME_DIR}" "${HOST_CONTROLLER_RESULTS_DIR}" "${HOST_RES_DIR}/runtime/${LAB_NAME}/generated"
  local host_bind="127.0.0.1"
  if is_true "${PROXY_BIND_ALL}"; then
    host_bind="0.0.0.0"
  fi
  python3 - "$HOST_RUNTIME_DIR/info" <<PY_INFO
import json, time, pathlib, sys
path = pathlib.Path(sys.argv[1])
def b(value):
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}
data = {
    "schema_version": 1,
    "status": "$status",
    "lab_name": "$LAB_NAME",
    "cluster_target": "$CLUSTER_TARGET",
    "controller_container": "$CONTROLLER_CONTAINER_NAME",
    "host_socket": b("${HOST_SOCKET}"),
    "expose_to_host": b("${EXPOSE_TO_HOST}"),
    "proxy_bind_all": b("${PROXY_BIND_ALL}"),
    "compose_project": "$COMPOSE_PROJECT_NAME",
    "kind_cluster": "$LAB_NAME",
    "docker_network": "$CP_NETWORK",
    "runtime_dir": "$RUNTIME_DIR",
    "results_dir": "$RESULTS_DIR",
    "host_runtime_dir": "$HOST_RUNTIME_DIR",
    "host_results_dir": "$HOST_CONTROLLER_RESULTS_DIR",
    "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    "controller_proxy": {
        "container_port": int("$CONTROLLER_PROXY_CONTAINER_PORT"),
        "host_bind": "$host_bind",
        "host_port": int("$host_port") if "$host_port" else None,
        "exposed": b("${EXPOSE_TO_HOST}")
    }
}
path.parent.mkdir(parents=True, exist_ok=True)
path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
PY_INFO
}

image_exists() { docker image inspect "$1" >/dev/null 2>&1; }
image_architecture() { docker image inspect --format '{{.Architecture}}' "$1" 2>/dev/null || true; }

ensure_network() {
  local net="$1"
  if docker network ls --format '{{.Name}}' | grep -qx "${net}"; then
    log "Docker network '${net}' already exists."
    return 0
  fi
  log "Creating Docker network '${net}'."
  docker network create --label "honeypot.lab=${LAB_NAME}" "${net}" >/dev/null
}

build_controller_if_needed() {
  local image_arch=""
  if image_exists "${CONTROLLER_IMAGE}"; then
    image_arch="$(image_architecture "${CONTROLLER_IMAGE}")"
  fi
  if [[ -n "${image_arch}" && "${image_arch}" != "amd64" ]]; then
    warn "Controller image '${CONTROLLER_IMAGE}' has unsupported architecture '${image_arch}', forcing rebuild for linux/amd64."
  fi
  if is_true "${BUILD_CONTROLLER}" || ! image_exists "${CONTROLLER_IMAGE}" || [[ "${image_arch}" != "amd64" ]]; then
    log "Building controller image '${CONTROLLER_IMAGE}'..."
    docker build --platform linux/amd64 -t "${CONTROLLER_IMAGE}" -f "${LAB_CONTROLLER_DIR}/Dockerfile" "${PROJECT_ROOT}/src"
  else
    log "Controller image '${CONTROLLER_IMAGE}' already present, skipping build."
  fi
}

extract_published_port() {
  local published
  published="$(docker port "${CONTROLLER_CONTAINER_NAME}" "${CONTROLLER_PROXY_CONTAINER_PORT}/tcp" 2>/dev/null | head -n1 || true)"
  [[ -n "${published}" ]] || return 0
  printf '%s\n' "${published##*:}"
}

start_controller() {
  local proxy_bind_addr="127.0.0.1"
  local -a docker_run_args=()
  if is_true "${PROXY_BIND_ALL}"; then
    proxy_bind_addr="0.0.0.0"
  fi

  mkdir -p \
    "${HOST_RES_DIR}" \
    "${HOST_RUNTIME_DIR}" \
    "${HOST_CONTROLLER_RESULTS_DIR}" \
    "${HOST_CONTROLLER_DOCKER_DATA_DIR}" \
    "${HOST_BUILD_HELPER_CACHE_DIR}"

  docker_run_args=(
    --name "${CONTROLLER_CONTAINER_NAME}"
    --hostname "${CONTROLLER_CONTAINER_NAME}"
    --label "honeypot.lab=${LAB_NAME}"
    --label "honeypot.role=controller"
    --privileged
    --cgroupns=host
    --restart unless-stopped
    --network "${CP_NETWORK}"
    --add-host host.docker.internal:host-gateway
    -e "CODE_ROOT=${CONTROLLER_CODE_DIR}"
    -e "ENV_FILE=${CONTROLLER_ENV_FILE}"
    -e "LAB_NAME=${LAB_NAME}"
    -e "CLUSTER_PROFILE=${LAB_NAME}"
    -e "RES_DIR=${RES_DIR}"
    -e "RUNTIME_DIR=${RUNTIME_DIR}"
    -e "RESULTS_DIR=${RESULTS_DIR}"
    -e "GENERATED_DIR=${GENERATED_DIR}"
    -e "STATE_FILE=${STATE_FILE}"
    -e "COMPOSE_PROJECT_NAME=${COMPOSE_PROJECT_NAME}"
    -e "CP_NETWORK=${CP_NETWORK}"
    -e "KUBE_CONTEXT=kind-${LAB_NAME}"
    -e "DOCKER_DATA_ROOT=${CONTROLLER_DOCKER_DATA_ROOT}"
    -e "CONTROLLER_PROXY_CONTAINER_PORT=${CONTROLLER_PROXY_CONTAINER_PORT}"
    -e "EXPOSE_TO_HOST=${EXPOSE_TO_HOST}"
    -e "PROXY_BIND_ALL=${PROXY_BIND_ALL}"
    -e "HOST_SOCKET=${HOST_SOCKET}"
    -e "BUILD_HELPER_CACHE_DIR=${CONTROLLER_BUILD_HELPER_CACHE_DIR}"
    -e "HOST_CODE_ROOT=${HOST_CODE_ROOT}"
    -e "HOST_RES_DIR=${HOST_RES_DIR}"
    -e "HOST_RUNTIME_DIR=${HOST_RUNTIME_DIR}"
    -e "HOST_CONTROLLER_RESULTS_DIR=${HOST_CONTROLLER_RESULTS_DIR}"
    -e "HOST_CONTROLLER_DOCKER_DATA_DIR=${HOST_CONTROLLER_DOCKER_DATA_DIR}"
    -e "HOST_BUILD_HELPER_CACHE_DIR=${HOST_BUILD_HELPER_CACHE_DIR}"
    -v "${HOST_RES_DIR}:${RES_DIR}:Z"
    -v "${HOST_CODE_ROOT}:${CONTROLLER_CODE_DIR}:Z"
    -v "${HOST_COMMON_CODE_DIR}:${CONTROLLER_COMMON_CODE_DIR}:Z"
  )

  if is_true "${HOST_SOCKET}"; then
    [[ -S /var/run/docker.sock ]] || err "HOST_SOCKET=true but host Docker socket not found at /var/run/docker.sock"
    log "Mounting host Docker socket into controller."
    docker_run_args+=(-v "/var/run/docker.sock:/var/run/docker.sock")
  else
    log "Host Docker socket sharing disabled. The controller will use its internal Docker daemon."
  fi

  if is_true "${EXPOSE_TO_HOST}"; then
    docker_run_args+=(-p "${proxy_bind_addr}::${CONTROLLER_PROXY_CONTAINER_PORT}")
  else
    log "EXPOSE_TO_HOST=false; controller proxy will not be published on the host."
  fi

  docker_run_args+=("${CONTROLLER_IMAGE}")

  log "Starting controller container '${CONTROLLER_CONTAINER_NAME}'."
  docker run -d "${docker_run_args[@]}" >/dev/null
}

check_host_socket_conflicts
if docker ps -a --format '{{.Names}}' | grep -qx "${CONTROLLER_CONTAINER_NAME}"; then
  err "Controller container '${CONTROLLER_CONTAINER_NAME}' already exists. Remove it with './remove_all.sh ${LAB_NAME}' before starting a new instance."
fi

build_controller_if_needed
ensure_network "${CP_NETWORK}"
write_info_file "starting"
start_controller

HOST_PORT=""
if is_true "${EXPOSE_TO_HOST}"; then
  HOST_PORT="$(extract_published_port)"
  [[ -n "${HOST_PORT}" ]] || err "Controller proxy port was not published by Docker."
fi
write_info_file "running" "${HOST_PORT}"

if [[ -n "${HOST_PORT}" ]]; then
  log "Controller '${CONTROLLER_CONTAINER_NAME}' started. Proxy exposed on $(is_true "$PROXY_BIND_ALL" && printf '0.0.0.0' || printf '127.0.0.1'):${HOST_PORT}."
else
  log "Controller '${CONTROLLER_CONTAINER_NAME}' started. Proxy is not exposed on the host."
fi

if is_true "${FOLLOW_CONTROLLER_LOGS}"; then
  docker logs -f "${CONTROLLER_CONTAINER_NAME}"
fi
