#!/bin/bash

PROJECT_ROOT=$1
REGISTRY_NAME=$2
REGISTRY_PORT=$3

ensure_registry_hosts() {
  local ip="127.0.0.1"
  local hosts="/etc/hosts"

  # serve sudo se non lanci come root
  if grep -Eq "^[[:space:]]*${ip//./\\.}[[:space:]]+${REGISTRY_NAME}([[:space:]]|\$)" "$hosts"; then
    warn "${REGISTRY_NAME} è già mappato a ${ip} in ${hosts}"
    return 0
  fi

  # se esiste una riga per 'registry' con un altro IP, la sostituiamo
  if grep -Eq "^[[:space:]]*[0-9.:a-fA-F]+[[:space:]]+${REGISTRY_NAME}([[:space:]]|\$)" "$hosts"; then
    log "Aggiorno mapping esistente per ${REGISTRY_NAME} in ${hosts}"
    sudo sed -i.bak -E "s|^[[:space:]]*[0-9.:a-fA-F]+[[:space:]]+(${REGISTRY_NAME})([[:space:]]|\$)|${ip}\t\1\2|" "$hosts"
  else
    log "Aggiungo mapping ${REGISTRY_NAME} -> ${ip} in ${hosts}"
    echo -e "${ip}\t${REGISTRY_NAME}" | sudo tee -a "$hosts" >/dev/null
  fi
}

# Verifica che la rete kind esista
if ! docker network inspect kind >/dev/null 2>&1; then
  err "Rete Docker 'kind' non trovata. Avvia il cluster kind prima di procedere."
  exit 1
fi

if docker ps -a --format '{{.Names}}' | grep -q "^$REGISTRY_NAME$"; then
  warn "Container registry già esistente."
else
  log "Avvio del registry Docker..."
  docker run -d --restart=always --name $REGISTRY_NAME --network kind \
    -v "${PROJECT_ROOT}/pb/auth:/auth" \
    -v "${PROJECT_ROOT}/pb/certs:/certs" \
    -e REGISTRY_STORAGE_DELETE_ENABLED=true \
    -e REGISTRY_HTTP_ADDR=0.0.0.0:${REGISTRY_PORT} \
    -e "REGISTRY_AUTH=htpasswd" \
    -e "REGISTRY_AUTH_HTPASSWD_REALM=Registry Realm" \
    -e "REGISTRY_AUTH_HTPASSWD_PATH=/auth/htpasswd" \
    -e "REGISTRY_HTTP_TLS_CERTIFICATE=/certs/domain.crt" \
    -e "REGISTRY_HTTP_TLS_KEY=/certs/domain.key" \
    -p ${REGISTRY_PORT}:${REGISTRY_PORT} \
    registry:2
fi

if docker network inspect kind | grep -q "\"Name\": \"$REGISTRY_NAME\""; then
  log "Registry già connesso alla rete kind."
else
  warn "Connessione del registry alla rete Kind..."
  docker network connect kind "$REGISTRY_NAME" || true
fi

ensure_registry_hosts
log "Login a ${REGISTRY_NAME}:${REGISTRY_PORT}..."
docker login ${REGISTRY_NAME}:${REGISTRY_PORT} -u "$REGISTRY_USER" -p "$REGISTRY_PASS"