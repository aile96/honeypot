#!/bin/bash

HTPASSWD_PATH=$1
REGISTRY_USER=$2
REGISTRY_PASS=$3
CERT_CRT_PATH=$4
CERT_KEY_PATH=$5
REGISTRY_CN=$6

if [ ! -f "$HTPASSWD_PATH" ]; then
  log "Generazione file htpasswd..."
  mkdir -p "$(dirname "$HTPASSWD_PATH")"
  htpasswd -Bbn "$REGISTRY_USER" "$REGISTRY_PASS" > "$HTPASSWD_PATH"
else
  warn "File htpasswd già esistente."
fi

if [ ! -f "$CERT_CRT_PATH" ] || [ ! -f "$CERT_KEY_PATH" ]; then
  log "Generazione certificati TLS self-signed..."
  mkdir -p "$(dirname "$CERT_CRT_PATH")"
  mkdir -p "$(dirname "$CERT_KEY_PATH")"
  openssl req -newkey rsa:4096 -nodes -sha256 \
    -keyout "$CERT_KEY_PATH" \
    -x509 -days 365 \
    -out "$CERT_CRT_PATH" \
    -subj "/CN=$REGISTRY_CN"
else
  warn "Certificati TLS già esistenti."
fi