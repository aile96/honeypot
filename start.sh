#!/usr/bin/env bash
set -euo pipefail

SCRIPT_START_EPOCH="$(date +%s)"
echo "[$(date '+%Y-%m-%d %H:%M:%S %Z')] Script started"

on_exit() {
  local exit_code=$?
  local script_end_epoch end_ts duration

  script_end_epoch="$(date +%s)"
  end_ts="$(date '+%Y-%m-%d %H:%M:%S %Z')"
  duration=$((script_end_epoch - SCRIPT_START_EPOCH))

  if [[ $exit_code -eq 0 ]]; then
    echo "[$end_ts] Pipeline completed (duration: ${duration}s)"
  else
    echo "[$end_ts] Script failed with exit code ${exit_code} (duration: ${duration}s)" >&2
  fi
}

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
IMAGE_VERSION=2.0.2
export PROJECT_ROOT IMAGE_VERSION

# === Load shared utilities ===
COMMON_LIB="${PROJECT_ROOT}/pb/scripts/lib/common.sh"
if [[ ! -f "$COMMON_LIB" ]]; then
  printf "[ERROR] Common library not found: %s\n" "$COMMON_LIB" >&2
  exit 1
fi
source "$COMMON_LIB"
trap_add on_exit EXIT

# === Load variables from config file ===
CONFIG_LOADER="${PROJECT_ROOT}/pb/scripts/lib/load_config.sh"
if [[ ! -f "$CONFIG_LOADER" ]]; then
  err "Configuration loader not found: $CONFIG_LOADER"
  exit 1
fi
source "$CONFIG_LOADER"

ENV_FILE="${PROJECT_ROOT}/configuration.conf"
if ! load_env_file "$ENV_FILE"; then
  err "Failed to load configuration from: $ENV_FILE"
  exit 1
fi

STEP_RETRY_ATTEMPTS="${STEP_RETRY_ATTEMPTS:-1}"
STEP_RETRY_DELAY_SECONDS="${STEP_RETRY_DELAY_SECONDS:-0}"

validate_retry_value() {
  local name="$1"
  local value="$2"
  local min="$3"
  [[ "${value}" =~ ^[0-9]+$ ]] || die "${name} must be an integer >= ${min} (got '${value}')."
  (( value >= min )) || die "${name} must be >= ${min} (got '${value}')."
}

step_retry_key() {
  local step_name
  step_name="$(basename "$1")"
  step_name="${step_name%.sh}"
  step_name="${step_name^^}"
  step_name="${step_name//[^A-Z0-9]/_}"
  printf '%s' "${step_name}"
}

resolve_step_retry_policy() {
  local step_script="$1"
  local step_key attempts_var delay_var attempts delay
  local attempts_label="STEP_RETRY_ATTEMPTS"
  local delay_label="STEP_RETRY_DELAY_SECONDS"

  step_key="$(step_retry_key "${step_script}")"
  attempts_var="STEP_RETRY_ATTEMPTS_${step_key}"
  delay_var="STEP_RETRY_DELAY_SECONDS_${step_key}"

  if [[ "${!attempts_var+x}" == "x" ]]; then
    attempts="${!attempts_var}"
    attempts_label="${attempts_var}"
  else
    attempts="${STEP_RETRY_ATTEMPTS}"
  fi
  if [[ "${!delay_var+x}" == "x" ]]; then
    delay="${!delay_var}"
    delay_label="${delay_var}"
  else
    delay="${STEP_RETRY_DELAY_SECONDS}"
  fi

  validate_retry_value "${attempts_label}" "${attempts}" 1
  validate_retry_value "${delay_label}" "${delay}" 0

  printf '%s;%s' "${attempts}" "${delay}"
}

source_step_once() {
  local step_script="$1"
  [[ -f "${step_script}" ]] || die "Step script not found: ${step_script}"
  log "Running step: $(basename "${step_script}")"
  source "${step_script}"
}

run_step_with_retry() {
  local step_script="$1"
  local step_name policy attempts delay

  step_name="$(basename "${step_script}")"
  policy="$(resolve_step_retry_policy "${step_script}")"
  attempts="${policy%%;*}"
  delay="${policy##*;}"

  log "Step retry policy for ${step_name}: attempts=${attempts}, delay=${delay}s."
  retry_cmd "${attempts}" "${delay}" "step ${step_name}" source_step_once "${step_script}"
}

STEP_SCRIPTS=(
  "${PROJECT_ROOT}/pb/scripts/00_ensure_deps.sh"
  "${PROJECT_ROOT}/pb/scripts/01_kind_cluster.sh"
  "${PROJECT_ROOT}/pb/scripts/02_setup_underlay.sh"
  "${PROJECT_ROOT}/pb/scripts/03_run_underlay.sh"
  "${PROJECT_ROOT}/pb/scripts/04_build_deploy.sh"
  "${PROJECT_ROOT}/pb/scripts/05_setup_k8s.sh"
)

for step_script in "${STEP_SCRIPTS[@]}"; do
  run_step_with_retry "${step_script}"
done
