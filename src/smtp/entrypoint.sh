#!/usr/bin/env bash
set -euo pipefail

# Prepara sshd (host keys generate on first run)
mkdir -p /var/run/sshd
chmod 0755 /var/run/sshd

# Se esiste /root/.ssh/authorized_keys assicura i permessi corretti
if [ -f /root/.ssh/authorized_keys ]; then
  chown -R root:root /root/.ssh
  chmod 0700 /root/.ssh
  chmod 0600 /root/.ssh/authorized_keys
fi

# Avvia sshd in background
/usr/sbin/sshd

# Avvia il server Python (foreground)
exec python /app/server.py
