#!/usr/bin/env bash
set -euo pipefail

# Env con default sensati (sovrascrivibili da compose)
: "${SAMBA_USER:=k8s}"
: "${SAMBA_PASS:=password}"
: "${SAMBA_UID:=10001}"
: "${SAMBA_GID:=10001}"
: "${SHARE_NAME:=pvroot}"
: "${SHARE_PATH:=/share}"
: "${HOSTS_ALLOW:=127. 172.18.0. 172.19.0.}"   # tipico per rete kind
: "${ENCRYPTION:=required}"  # required|desired
: "${LOG_LEVEL:=1}"

# Crea gruppo/utente se non esistono
if ! getent group "${SAMBA_GID}" >/dev/null 2>&1 ; then
  groupadd -g "${SAMBA_GID}" "${SAMBA_USER}"
fi
if ! id -u "${SAMBA_UID}" >/dev/null 2>&1 ; then
  useradd -m -u "${SAMBA_UID}" -g "${SAMBA_GID}" -s /usr/sbin/nologin "${SAMBA_USER}"
fi

chown -R "${SAMBA_UID}:${SAMBA_GID}" "${SHARE_PATH}" /data

# Imposta password Samba per l'utente
(echo "${SAMBA_PASS}"; echo "${SAMBA_PASS}") | smbpasswd -a -s "${SAMBA_USER}"

# Genera smb.conf dal template
export SAMBA_USER SHARE_NAME SHARE_PATH HOSTS_ALLOW ENCRYPTION LOG_LEVEL
envsubst < /etc/samba/smb.conf.tmpl > /etc/samba/smb.conf

# Mostra config minimale a log
echo "==== Effective /etc/samba/smb.conf ===="
grep -vE '^\s*#' /etc/samba/smb.conf || true
echo "======================================="

exec "$@"
