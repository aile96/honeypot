#!/usr/bin/env bash
set -euo pipefail

# Env with sensible defaults (overridable by compose)
: "${SAMBA_USER:=k8s}"
: "${SAMBA_PASS:=password}"
: "${SAMBA_UID:=10001}"
: "${SAMBA_GID:=10001}"
: "${SHARE_NAME:=pvroot}"
: "${SHARE_PATH:=/share}"
: "${HOSTS_ALLOW:=127. 172.18.0. 172.19.0.}"   # typical for kind network
: "${ENCRYPTION:=required}"  # required|desired
: "${LOG_LEVEL:=1}"

# Create group/user if they do not exist
if ! getent group "${SAMBA_GID}" >/dev/null 2>&1 ; then
  groupadd -g "${SAMBA_GID}" "${SAMBA_USER}"
fi
if ! id -u "${SAMBA_UID}" >/dev/null 2>&1 ; then
  useradd -m -u "${SAMBA_UID}" -g "${SAMBA_GID}" -s /usr/sbin/nologin "${SAMBA_USER}"
fi

chown -R "${SAMBA_UID}:${SAMBA_GID}" "${SHARE_PATH}" /data

# Set Samba password for the user
(echo "${SAMBA_PASS}"; echo "${SAMBA_PASS}") | smbpasswd -a -s "${SAMBA_USER}"

# Generate smb.conf from template
export SAMBA_USER SHARE_NAME SHARE_PATH HOSTS_ALLOW ENCRYPTION LOG_LEVEL
envsubst < /etc/samba/smb.conf.tmpl > /etc/samba/smb.conf

# Print minimal config to logs
echo "==== Effective /etc/samba/smb.conf ===="
grep -vE '^\s*#' /etc/samba/smb.conf || true
echo "======================================="

exec "$@"
