#!/usr/bin/env bash
set -euo pipefail

# percorso al file .env (puoi cambiarlo se non sta nella root del progetto)
ENV_FILE=$1
shift

if [[ -f "$ENV_FILE" ]]; then
  log "Carico variabili da $ENV_FILE..."
  set -a                # esporta automaticamente tutte le variabili
  source "$ENV_FILE"    # importa il contenuto
  set +a
else
  err "Nessun file $ENV_FILE trovato"
fi

log "Avvio skaffold deployment..."
skaffold deploy --tag "$IMAGE_VERSION" --port-forward=user --tail "$@"