#!/usr/bin/env bash
set -euo pipefail

### === Utility functions ===
log() { printf "\033[1;36m[INFO]\033[0m %s\n" "$*"; }
warn() { printf "\033[1;33m[WARN]\033[0m %s\n" "$*"; }
err() { printf "\033[1;31m[ERR ]\033[0m %s\n" "$*" >&2; }
die()   { echo -e "[ERROR] $*" >&2; exit 1; }

# path to file .env
if [[ -f "$ENV_FILE" ]]; then
  log "Loading vars from $ENV_FILE..."
  set -a                # export all vars
  source "$ENV_FILE"    # content import
  set +a
else
  err "No file $ENV_FILE found"
fi

log "Running skaffold deployment..."
skaffold run --tag "$IMAGE_VERSION" "$@"