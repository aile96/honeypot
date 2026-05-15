#!/usr/bin/env bash
# Exit immediately on errors, undefined variables, or failed pipeline commands.
set -euo pipefail

# --------------------------
# 0) Define the project root and common library path.
# --------------------------
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMMON_LIB="${PROJECT_ROOT}/src/lab/lib-bash/common.sh"

# --------------------------
# 1) Load common helper functions
# --------------------------
if [[ ! -f "${COMMON_LIB}" ]]; then
  printf "[ERROR] Common library not found: %s\n" "${COMMON_LIB}" >&2
  exit 1
fi
source "${COMMON_LIB}"
log "Common library loaded from ${COMMON_LIB}."


# --------------------------
# 2) Load the main project configuration file
# --------------------------
ENV_FILE="${PROJECT_ROOT}/configuration.conf"
if ! load_env_file "$ENV_FILE"; then
  err "Failed to load configuration from: $ENV_FILE"
  exit 1
fi
log "Configuration loaded from ${ENV_FILE}."


# --------------------------
# 3) Define default variables
# --------------------------
# Default values for configuration variables.
CLUSTER_TARGET="${CLUSTER_TARGET:-5Gcore}"
CLUSTER_PROFILE="${CLUSTER_PROFILE:-honeypotlab}"
GENERIC_SVC_PORT="${GENERIC_SVC_PORT:-8085}"
FOLLOW_CONTROLLER_LOGS="${FOLLOW_CONTROLLER_LOGS:-true}"
SKIP_RESOURCE_CHECK="${SKIP_RESOURCE_CHECK:-true}"
BUILD_CONTROLLER="${BUILD_CONTROLLER:-false}"
PROXY_BIND_ALL="${PROXY_BIND_ALL:-true}"
EXPOSE_TO_HOST="${EXPOSE_TO_HOST:-true}"
HOST_SOCKET="${HOST_SOCKET:-false}"
# Paths for volumes and other resources.
CONTROLLER_CODE_DIR="/workdir/code"
CONTROLLER_COMMON_CODE_DIR="/workdir/common"
LAB_CONTROLLER_DIR="${PROJECT_ROOT}/src/lab/lab-controller"
CONTROLLER_ENV_FILE="${CONTROLLER_CODE_DIR}/conf-files/variables.py"
RES_DIR="/res"
HOST_RES_DIR="${PROJECT_ROOT}/${RES_DIR#/}"
HOST_CODE_ROOT="${PROJECT_ROOT}/src/${CLUSTER_TARGET}"
HOST_COMMON_CODE_DIR="${PROJECT_ROOT}/src/common"
HOST_RUNTIME_DIR="${HOST_RES_DIR}/runtime"
HOST_CONTROLLER_RESULTS_DIR="${HOST_RES_DIR}/results"
HOST_CONTROLLER_DOCKER_DATA_DIR="${HOST_RES_DIR}/cache/images"
HOST_BUILD_HELPER_CACHE_DIR="${HOST_RES_DIR}/cache/build-helper"
CONTROLLER_DOCKER_DATA_ROOT="${RES_DIR}/cache/images"
CONTROLLER_BUILD_HELPER_CACHE_DIR="${RES_DIR}/cache/build-helper"
# Variables for the script itself.
CONTROLLER_CONTAINER_NAME="${CONTROLLER_CONTAINER_NAME:-${CLUSTER_PROFILE}-controller}"
CP_NETWORK="${CP_NETWORK:-bridge}"
REQUIRED_AVAIL_MEM_MB="${REQUIRED_AVAIL_MEM_MB:-16384}" # 16 GB
REQUIRED_CPUS="${REQUIRED_CPUS:-6}"
log "Variables set. Cluster target: ${CLUSTER_TARGET}, profile: ${CLUSTER_PROFILE}."


# --------------------------
# 4) Validate the loaded configuration variables
# --------------------------
# Validate Docker container name.
[[ "${CLUSTER_PROFILE}" =~ ^[A-Za-z0-9][A-Za-z0-9_.-]*$ ]] || err "Invalid CLUSTER_PROFILE='${CLUSTER_PROFILE}' for Docker container name."
[[ "${CONTROLLER_CONTAINER_NAME}" =~ ^[A-Za-z0-9][A-Za-z0-9_.-]*$ ]] || err "Invalid CONTROLLER_CONTAINER_NAME='${CONTROLLER_CONTAINER_NAME}' for Docker container name."

# Validate CLUSTER_TARGET to avoid unsafe path or shell values.
[[ "${CLUSTER_TARGET}" =~ ^[A-Za-z0-9._-]+$ ]] || err "Invalid CLUSTER_TARGET='${CLUSTER_TARGET}'."

# Target-specific configuration file.
TARGET_CONFIG_FILE="${PROJECT_ROOT}/src/${CLUSTER_TARGET}/conf-files/variables.py"
TARGET_COMPOSE_FILE="${PROJECT_ROOT}/src/${CLUSTER_TARGET}/conf-files/compose.yaml"
TARGET_KIND_TEMPLATE="${PROJECT_ROOT}/src/${CLUSTER_TARGET}/conf-files/kind-cluster.yaml.tmpl"
TARGET_SKAFFOLD_TEMPLATE="${PROJECT_ROOT}/src/${CLUSTER_TARGET}/conf-files/skaffold.yaml.tmpl"

# Ensure the target configuration files exist.
[[ -f "${TARGET_CONFIG_FILE}" ]] || err "Target configuration file not found: ${TARGET_CONFIG_FILE}"
[[ -f "${TARGET_COMPOSE_FILE}" ]] || err "Target compose file not found: ${TARGET_COMPOSE_FILE}"
[[ -f "${TARGET_KIND_TEMPLATE}" ]] || err "Target kind template not found: ${TARGET_KIND_TEMPLATE}"
[[ -f "${TARGET_SKAFFOLD_TEMPLATE}" ]] || err "Target skaffold template not found: ${TARGET_SKAFFOLD_TEMPLATE}"

# Check if directory is well structured with expected files.
[[ -d "${PROJECT_ROOT}/src/${CLUSTER_TARGET}/containers" ]] || err "Expected 'containers' directory not found for target '${CLUSTER_TARGET}' at ${PROJECT_ROOT}/src/${CLUSTER_TARGET}/containers"
[[ -d "${PROJECT_ROOT}/src/${CLUSTER_TARGET}/helm-charts" ]] || err "Expected 'helm-charts' directory not found for target '${CLUSTER_TARGET}' at ${PROJECT_ROOT}/src/${CLUSTER_TARGET}/helm-charts"
[[ -d "${HOST_COMMON_CODE_DIR}" ]] || err "Expected common directory not found at ${HOST_COMMON_CODE_DIR}"

# Normalize boolean variables so they can be safely tested later.
normalize_bool_var SKIP_RESOURCE_CHECK
normalize_bool_var FOLLOW_CONTROLLER_LOGS
normalize_bool_var EXPOSE_TO_HOST
normalize_bool_var BUILD_CONTROLLER
normalize_bool_var PROXY_BIND_ALL
normalize_bool_var HOST_SOCKET
require_port_var GENERIC_SVC_PORT

log "Configuration variables validated and normalized. SKIP_RESOURCE_CHECK=${SKIP_RESOURCE_CHECK}, FOLLOW_CONTROLLER_LOGS=${FOLLOW_CONTROLLER_LOGS}, EXPOSE_TO_HOST=${EXPOSE_TO_HOST}, BUILD_CONTROLLER=${BUILD_CONTROLLER}, PROXY_BIND_ALL=${PROXY_BIND_ALL}, HOST_SOCKET=${HOST_SOCKET}."

# --------------------------
# 5) Check if docker exists, the lab is not running already and resource are enough to start the lab
# --------------------------
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
  if command -v nproc >/dev/null 2>&1; then
    nproc
    return 0
  fi
  if command -v sysctl >/dev/null 2>&1; then
    sysctl -n hw.ncpu
    return 0
  fi
  return 1
}

check_system_resources() {
  local avail_mem_mb cpu_count

  avail_mem_mb="$(get_mem_available_mb || true)"
  cpu_count="$(get_cpu_count || true)"

  if [[ -z "${avail_mem_mb}" || "${avail_mem_mb}" -le 0 ]]; then
    err "Unable to determine available RAM. Need at least ${REQUIRED_AVAIL_MEM_MB} MB available."
  fi
  if (( avail_mem_mb < REQUIRED_AVAIL_MEM_MB )); then
    err "Not enough available RAM: ${avail_mem_mb} MB available, need at least ${REQUIRED_AVAIL_MEM_MB} MB."
  fi

  if [[ -z "${cpu_count}" || "${cpu_count}" -le 0 ]]; then
    err "Unable to determine CPU count. Need at least ${REQUIRED_CPUS} CPUs."
  fi
  if (( cpu_count < REQUIRED_CPUS )); then
    err "Not enough CPU cores: ${cpu_count} available, need at least ${REQUIRED_CPUS}."
  fi
}

# Require Docker to be installed and available.
req docker

if is_false "${SKIP_RESOURCE_CHECK}"; then
  log "Checking system resources before starting the lab..."
  check_system_resources
  log "System resource check passed."
else
  log "Skipping system resource check as per configuration."
fi

# Check on existing lab.
if docker ps -a --format '{{.Names}}' | grep -qx "${CONTROLLER_CONTAINER_NAME}"; then
  err "Controller container '${CONTROLLER_CONTAINER_NAME}' is already running. Please remove it with './remove_all.sh' before starting a new instance."
fi

log "No existing controller container named '${CONTROLLER_CONTAINER_NAME}' found. Proceeding with startup."


# --------------------------
# 6) Build and start the lab controller container
# --------------------------
# Check whether a Docker image exists locally.
image_exists() {
  local img="$1"
  docker image inspect "${img}" >/dev/null 2>&1
}

image_architecture() {
  local img="$1"
  docker image inspect --format '{{.Architecture}}' "${img}" 2>/dev/null || true
}

# Create a Docker network if it does not already exist.
ensure_network() {
  local net="$1"

  if docker network ls --format '{{.Name}}' | grep -qx "${net}"; then
    log "Docker network '${net}' already exists."
    return 0
  fi

  log "Creating Docker network '${net}'."
  docker network create "${net}" >/dev/null
}

# Build the controller Docker image if requested or if the image is missing.
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
    docker build \
      --platform linux/amd64 \
      -t "${CONTROLLER_IMAGE}" \
      -f "${LAB_CONTROLLER_DIR}/Dockerfile" \
      "${PROJECT_ROOT}/src"
  else
    log "Controller image '${CONTROLLER_IMAGE}' already present, skipping build."
  fi
}

# Start the lab controller container.
start_controller() {
  local proxy_bind_addr="127.0.0.1"
  local controller_network="${CP_NETWORK}"
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

    --privileged
    --cgroupns=host
    --restart unless-stopped

    -e "CODE_ROOT=${CONTROLLER_CODE_DIR}"
    -e "ENV_FILE=${CONTROLLER_ENV_FILE}"
    -e "CLUSTER_PROFILE=${CLUSTER_PROFILE}"
    -e "RES_DIR=${RES_DIR}"
    -e "DOCKER_DATA_ROOT=${CONTROLLER_DOCKER_DATA_ROOT}"
    -e "GENERIC_SVC_PORT=${GENERIC_SVC_PORT}"
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
    if [[ ! -S /var/run/docker.sock ]]; then
      err "HOST_SOCKET=true but host Docker socket not found at /var/run/docker.sock"
    fi

    log "Mounting host Docker socket into controller."
    log "Using host network for controller so Docker-published localhost ports stay reachable."
    controller_network="host"
    docker_run_args+=(
      --network "${controller_network}"
      -v "/var/run/docker.sock:/var/run/docker.sock"
    )
  else
    log "Host Docker socket sharing disabled."
    log "Controller Docker image cache will persist on host at ${HOST_CONTROLLER_DOCKER_DATA_DIR}."
    docker_run_args+=(--network "${controller_network}")

    if is_true "${EXPOSE_TO_HOST}"; then
      docker_run_args+=(
        -p "${proxy_bind_addr}:8888:8888"
        -p "${proxy_bind_addr}:8080:8080"
        -p "${proxy_bind_addr}:6443:6443"
        -p "${proxy_bind_addr}:${GENERIC_SVC_PORT}:${GENERIC_SVC_PORT}"
      )
    else
      log "EXPOSE_TO_HOST=false; controller ports will not be published on the host."
    fi
  fi


  docker_run_args+=(
    "${CONTROLLER_IMAGE}"
  )

  log "Starting controller container '${CONTROLLER_CONTAINER_NAME}'."
  docker run -d "${docker_run_args[@]}" >/dev/null
}

CONTROLLER_IMAGE="${CONTROLLER_IMAGE:-lab-controller:latest}"
build_controller_if_needed
ensure_network "${CP_NETWORK}"
start_controller

log "Controller '${CONTROLLER_CONTAINER_NAME}' started."

# Optionally follow the controller logs.
if is_true "${FOLLOW_CONTROLLER_LOGS}"; then
  docker logs -f "${CONTROLLER_CONTAINER_NAME}"
fi
