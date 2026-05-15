#!/usr/bin/env bash
set -euo pipefail

# Build nginx.conf from the template using envsubst
export PRIMARY_ADDR MIRROR_ADDR CONNECT_TIMEOUT READ_TIMEOUT SEND_TIMEOUT

# Get a resolver from /etc/resolv.conf to resolve MIRROR_ADDR (DNS)
RESOLVER_IP="$(awk '/^nameserver/{print $2; exit}' /etc/resolv.conf || true)"
if [[ -z "${RESOLVER_IP:-}" ]]; then
  RESOLVER_IP="8.8.8.8"
fi
export RESOLVER_IP

envsubst '\
$PRIMARY_ADDR \
$MIRROR_ADDR \
$CONNECT_TIMEOUT \
$READ_TIMEOUT \
$SEND_TIMEOUT \
$RESOLVER_IP' \
  < /etc/nginx/templates/nginx.conf.tmpl \
  > /etc/nginx/nginx.conf

exec "$@"
