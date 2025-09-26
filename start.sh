#!/usr/bin/env bash
set -euo pipefail

### === Funzioni di utilità ===
log() { printf "\033[1;36m[INFO]\033[0m %s\n" "$*"; }
warn() { printf "\033[1;33m[WARN]\033[0m %s\n" "$*"; }
err() { printf "\033[1;31m[ERR ]\033[0m %s\n" "$*" >&2; }
die()   { echo -e "[ERROR] $*" >&2; exit 1; }
retry_func() {
  local max_retries="${RETRY_MAX:-30}"
  local delay="${RETRY_DELAY:-10}"
  local attempt=1

  while true; do
    # esegui in subshell con set +e per catturare sempre l'exit code
    (
      set +e
      "$@"
    )
    local rc=$?

    if [ $rc -eq 0 ]; then
      return 0
    fi

    if (( attempt >= max_retries )); then
      die "Funzione/Comando fallito dopo ${max_retries} tentativi: $* (rc=$rc)"
    fi

    warn "Fallito (tentativo ${attempt}/${max_retries}, rc=$rc). Riprovo tra ${delay}s… → $*"
    sleep "$delay"
    attempt=$((attempt + 1))
  done
}
export -f log warn err die retry_func

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
bash "${PROJECT_ROOT}/scripts/01_kind_cluster.sh" "$CLUSTER_NAME" "$NUM_WORKERS" "$REGISTRY_NAME" "$REGISTRY_PORT" "$REGISTRY_USER" "$REGISTRY_PASS" "$REGISTRY_MALICIOUS_NAME" "$CILIUM_HELM_VERSION" "$KUBE_SYSTEM_NS" "$POOL_IP" "$LABEL_NODE_ATTACKER" "$LABEL_NOT_ATTACKER"
bash "${PROJECT_ROOT}/scripts/02_credentials_tls.sh" "$HTPASSWD_PATH" "$REGISTRY_USER" "$REGISTRY_PASS" "$CERT_CRT_PATH" "$CERT_KEY_PATH" "$REGISTRY_CN"
bash "${PROJECT_ROOT}/scripts/03_registry.sh" "$PROJECT_ROOT" "$REGISTRY_NAME" "$REGISTRY_PORT"
bash "${PROJECT_ROOT}/scripts/04_skaffold_build.sh" "$ENV_FILE"
bash "${PROJECT_ROOT}/scripts/05_docker_network.sh" "$CALDERA_SERVER" "$CALDERA_UNDERLAY" "$CALDERA_OUTSIDE" "$CALDERA_CONTROLLER" "$BRIDGE_NET" "$ROUTER_NAME" "$LOAD_GENERATOR" "$CLUSTER_NAME"
bash "${PROJECT_ROOT}/scripts/06_skaffold_deploy.sh" "$ENV_FILE"

log "Pipeline completata."