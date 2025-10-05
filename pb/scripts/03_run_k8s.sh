#!/usr/bin/env bash
set -euo pipefail

### === Funzioni di utilità ===
log() { printf "\033[1;36m[INFO]\033[0m %s\n" "$*"; }
warn() { printf "\033[1;33m[WARN]\033[0m %s\n" "$*"; }
err() { printf "\033[1;31m[ERR ]\033[0m %s\n" "$*" >&2; }
die()   { echo -e "[ERROR] $*" >&2; exit 1; }

# percorso al file .env (puoi cambiarlo se non sta nella root del progetto)
if [[ -f "$ENV_FILE" ]]; then
  log "Carico variabili da $ENV_FILE..."
  set -a                # esporta automaticamente tutte le variabili
  source "$ENV_FILE"    # importa il contenuto
  set +a
else
  err "Nessun file $ENV_FILE trovato"
fi

log "Avvio skaffold deployment..."
skaffold run --tag "$IMAGE_VERSION" --port-forward=user "$@"