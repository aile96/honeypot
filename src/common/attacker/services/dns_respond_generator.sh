#!/bin/bash
# helper for generating the dnsmasq conf (used by entrypoint)
CONTAINER_IP="$1"
LOG_DIR="$2"
cat > /etc/dnsmasq.d/99-all-respond.conf <<EOF
address=/#/${CONTAINER_IP}
log-queries
log-facility=${LOG_DIR}/dnsmasq.log
no-hosts
EOF
