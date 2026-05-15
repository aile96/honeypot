#!/usr/bin/env bash
# Exit immediately on errors, undefined variables, or failed pipeline commands.
set -euo pipefail

# Define the project root and common library path.
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
# 3) Define default variables and remove existing lab container if present
# --------------------------
CLUSTER_PROFILE="${CLUSTER_PROFILE:-honeypotlab}"
CONTROLLER_CONTAINER_NAME="${CONTROLLER_CONTAINER_NAME:-${CLUSTER_PROFILE}-controller}"

# Validate Docker container name.
[[ "${CLUSTER_PROFILE}" =~ ^[A-Za-z0-9][A-Za-z0-9_.-]*$ ]] || err "Invalid CLUSTER_PROFILE='${CLUSTER_PROFILE}' for Docker container name."
[[ "${CONTROLLER_CONTAINER_NAME}" =~ ^[A-Za-z0-9][A-Za-z0-9_.-]*$ ]] || err "Invalid CONTROLLER_CONTAINER_NAME='${CONTROLLER_CONTAINER_NAME}' for Docker container name."

req docker
docker ps >/dev/null 2>&1 || err "Docker daemon is not reachable. Start Docker and retry."

# Check on existing lab.
if docker ps -a --format '{{.Names}}' | grep -qx "${CONTROLLER_CONTAINER_NAME}"; then
  if docker ps --format '{{.Names}}' | grep -qx "${CONTROLLER_CONTAINER_NAME}"; then
    log "Stopping entrypoint.py inside '${CONTROLLER_CONTAINER_NAME}'."
    docker exec "${CONTROLLER_CONTAINER_NAME}" sh -lc \
      "pid=\"\$(ps -eo pid=,args= | awk '/entrypoint[.]py/ {print \$1; exit}')\"; if [ -n \"\$pid\" ]; then kill -TERM \"\$pid\"; fi" \
      >/dev/null 2>&1 || warn "Could not signal entrypoint.py inside '${CONTROLLER_CONTAINER_NAME}'."
    sleep 2
  fi

  log "Found existing controller container named '${CONTROLLER_CONTAINER_NAME}'. Removing..."
  docker rm -f "${CONTROLLER_CONTAINER_NAME}" >/dev/null 2>&1 || err "Failed to remove existing container '${CONTROLLER_CONTAINER_NAME}'."
else
  warn "No existing controller container named '${CONTROLLER_CONTAINER_NAME}' found. No need to remove."
fi

log "Cleanup completed."
