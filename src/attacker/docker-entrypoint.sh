#!/usr/bin/env bash
set -euo pipefail

# Parametri dockerd
: "${DOCKER_DIR:=/var/lib/docker}"
: "${DOCKER_HOST_UNIX:=unix:///var/run/docker.sock}"

if [[ "${DOCKER_DAEMON:-0}" == "1" ]]; then
  # Assicurati che le dir esistano
  mkdir -p /var/run "$DOCKER_DIR"
  
  # Avvia dockerd in background con:
  # - storage vfs
  # - socket unix separata dall'host
  # - API TCP solo su loopback per debug (opzionale)
  dockerd \
    --data-root="$DOCKER_DIR" \
    --host="$DOCKER_HOST_UNIX" \
    --storage-driver=vfs \
    --insecure-registry registry:5000 \
    >/var/log/dockerd.log 2>&1 &
  
  # Attendi la socket pronta
  echo "==> Waiting for Docker daemon..."
  tries=0
  until docker --host="$DOCKER_HOST_UNIX" info >/dev/null 2>&1; do
    sleep 0.5
    tries=$((tries+1))
    if [ "$tries" -gt 120 ]; then
      echo "ERROR: dockerd non parte. Ultime righe di log:"
      tail -n 100 /var/log/dockerd.log || true
      exit 1
    fi
  done
  echo "==> Docker daemon è pronto."
fi

# Esporta DOCKER_HOST per i processi successivi nel container
export DOCKER_HOST="$DOCKER_HOST_UNIX"

# Esegui il CMD originale
exec "$@"
