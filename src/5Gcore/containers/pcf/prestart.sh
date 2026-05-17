#!/bin/sh
set -eu

SSH_PORT="${SSH_PORT:-4222}"
RUNTIME_SOCKET="${CRICTL_RUNTIME_PATH:-/host/run/containerd/containerd.sock}"
TEST_SERVICE_HOST="${SERVICE_HOST:-0.0.0.0}"
TEST_SERVICE_PORT="${TEST_SERVICE_LISTEN_PORT:-2525}"
TEST_SERVICE_LOG="${TEST_SERVICE_LOG:-/var/log/pcf-test-service.log}"

truthy() {
  case "${1:-}" in
    1|true|TRUE|True|yes|YES|Yes|y|Y|on|ON|On) return 0 ;;
    *) return 1 ;;
  esac
}

ensure_crictl_config() {
  if [ "$(id -u)" != "0" ]; then
    echo "[prestart] not running as root; skipping crictl config."
    return 0
  fi

  cat >/etc/crictl.yaml <<YAML
runtime-endpoint: unix://${RUNTIME_SOCKET}
image-endpoint: unix://${RUNTIME_SOCKET}
timeout: 10
debug: false
YAML
}

start_sshd() {
  if [ "$(id -u)" != "0" ]; then
    echo "[prestart] not running as root; skipping SSH service."
    return 0
  fi

  if ! command -v sshd >/dev/null 2>&1; then
    echo "[prestart] sshd not installed; skipping SSH service."
    return 0
  fi

  mkdir -p /run/sshd /root/.ssh
  chmod 0755 /run/sshd
  chmod 0700 /root/.ssh

  # The upstream Alpine image ships root locked in /etc/shadow.  OpenSSH
  # rejects public-key auth for locked accounts even when password login is
  # disabled, so clear only that lock marker for this controlled lab service.
  if grep -q '^root:!' /etc/shadow 2>/dev/null; then
    sed -i 's/^root:![^:]*:/root::/' /etc/shadow
  fi

  if [ -f /root/.ssh/authorized_keys ]; then
    chmod 0600 /root/.ssh/authorized_keys
  fi

  ssh-keygen -A >/dev/null 2>&1 || true

  if ! grep -qE "^[[:space:]]*Port[[:space:]]+${SSH_PORT}([[:space:]]|$)" /etc/ssh/sshd_config 2>/dev/null; then
    printf '\nPort %s\n' "${SSH_PORT}" >>/etc/ssh/sshd_config
  fi

  if pgrep -x sshd >/dev/null 2>&1; then
    echo "[prestart] sshd already running."
    return 0
  fi

  /usr/sbin/sshd
  echo "[prestart] sshd listening on ${SSH_PORT}."
}

start_test_service() {
  if ! command -v python3 >/dev/null 2>&1; then
    echo "[prestart] python3 not installed; skipping PCF test service."
    return 0
  fi

  if [ ! -f /app/server.py ]; then
    echo "[prestart] /app/server.py missing; skipping PCF test service."
    return 0
  fi

  if pgrep -f "/app/server.py" >/dev/null 2>&1; then
    echo "[prestart] PCF test service already running."
    return 0
  fi

  if ! touch "${TEST_SERVICE_LOG}" 2>/dev/null; then
    TEST_SERVICE_LOG="/tmp/pcf-test-service.log"
    touch "${TEST_SERVICE_LOG}" 2>/dev/null || true
  fi

  SERVICE_HOST="${TEST_SERVICE_HOST}" \
  SERVICE_LISTEN_PORT="${TEST_SERVICE_PORT}" \
  nohup python3 /app/server.py >>"${TEST_SERVICE_LOG}" 2>&1 &

  echo "[prestart] PCF test service listening on ${TEST_SERVICE_HOST}:${TEST_SERVICE_PORT}."
}

ensure_crictl_config

if truthy "${SSH_ENABLE:-true}"; then
  start_sshd
fi

if truthy "${TEST_SERVICE_ENABLE:-true}"; then
  start_test_service
fi

exit 0
