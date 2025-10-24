#!/usr/bin/env bash
set -euo pipefail

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

source "${PROJECT_ROOT}/pb/scripts/00_install_deps.sh"
source "${PROJECT_ROOT}/pb/scripts/01_kind_cluster.sh"
source "${PROJECT_ROOT}/pb/scripts/02_setup_underlay.sh"
source "${PROJECT_ROOT}/pb/scripts/03_run_k8s.sh"

echo "Pipeline completed"