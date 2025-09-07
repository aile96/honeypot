#!/usr/bin/env bash
set -euo pipefail

### === Funzioni di utilità ===
log() { printf "\033[1;36m[INFO]\033[0m %s\n" "$*"; }
warn() { printf "\033[1;33m[WARN]\033[0m %s\n" "$*"; }
err() { printf "\033[1;31m[ERR ]\033[0m %s\n" "$*" >&2; }
export -f log warn err

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# === Carica variabili da config/config.sh (ignora commenti e righe vuote) ===
ENV_FILE="${PROJECT_ROOT}/project.env"
if [[ ! -f "$ENV_FILE" ]]; then
  err "File di configurazione non trovato: $ENV_FILE" >&2
  exit 1
fi

while IFS= read -r line; do
  [[ -z "$line" ]] && continue         # salta righe vuote
  [[ "$line" =~ ^# ]] && continue      # salta commenti
  export "$line"
done < "$ENV_FILE"

#bash "${PROJECT_ROOT}/scripts/00_install_deps.sh"
bash "${PROJECT_ROOT}/scripts/01_kind_cluster.sh" "$CLUSTER_NAME" "$NUM_WORKERS" "$REGISTRY_NAME" "$REGISTRY_PORT" "$REGISTRY_USER" "$REGISTRY_PASS"
bash "${PROJECT_ROOT}/scripts/02_helm_repos.sh" "$CILIUM_HELM_VERSION" "$KUBE_SYSTEM_NS" "$NUM_WORKERS"
#bash "${PROJECT_ROOT}/scripts/03_credentials_tls.sh" "$HTPASSWD_PATH" "$REGISTRY_USER" "$REGISTRY_PASS" "$CERT_CRT_PATH" "$CERT_KEY_PATH" "$REGISTRY_CN"
#bash "${PROJECT_ROOT}/scripts/04_registry.sh" "$PROJECT_ROOT" "$REGISTRY_NAME" "$REGISTRY_PORT"
#bash "${PROJECT_ROOT}/scripts/05_skaffold_build.sh" "$ENV_FILE"
#bash "${PROJECT_ROOT}/scripts/06_caldera.sh" "$CALDERA_SERVER" "$CALDERA_ATTACKER" "$CALDERA_IP"
#bash "${PROJECT_ROOT}/scripts/07_skaffold_deploy.sh" "$ENV_FILE"

log "Pipeline completata."