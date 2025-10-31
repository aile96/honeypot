#!/usr/bin/env bash
set -euo pipefail

# === Ask for sudo password once ===
if sudo -v; then
  # Keep sudo alive while the script runs
  while true; do sudo -n true; sleep 60; done 2>/dev/null &
  SUDO_KEEPALIVE_PID=$!
  trap 'kill $SUDO_KEEPALIVE_PID' EXIT
else
  echo "You must have sudo privileges or provide the correct password." >&2
  exit 1
fi

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export PROJECT_ROOT

# === Load variables from config file ===
ENV_FILE="${PROJECT_ROOT}/skaffold.env"
if [[ ! -f "$ENV_FILE" ]]; then
  err "Configuration file not found: $ENV_FILE" >&2
  exit 1
fi

while IFS= read -r line; do
  [[ -z "$line" ]] && continue         # skip empty lines
  [[ "$line" =~ ^# ]] && continue      # skip comments
  export "$line"
done < "$ENV_FILE"

#source "${PROJECT_ROOT}/pb/scripts/00_install_deps.sh"
source "${PROJECT_ROOT}/pb/scripts/01_kind_cluster.sh"
source "${PROJECT_ROOT}/pb/scripts/02_setup_underlay.sh"
source "${PROJECT_ROOT}/pb/scripts/03_run_k8s.sh"

echo "Pipeline completed"