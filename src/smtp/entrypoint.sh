#!/usr/bin/env bash
set -euo pipefail

# Prepare sshd (host keys generate on first run)
mkdir -p /var/run/sshd
chmod 0755 /var/run/sshd

# If /root/.ssh/authorized_keys exists, ensure correct permissions
if [ -f /root/.ssh/authorized_keys ]; then
  chown -R root:root /root/.ssh
  chmod 0700 /root/.ssh
  chmod 0600 /root/.ssh/authorized_keys
fi

# Start sshd in background
/usr/sbin/sshd

# Start the Python server (foreground)
exec python /app/server.py
