#!/usr/bin/env bash
set -euo pipefail

DOCKER_HOST_UNIX="unix:///var/run/docker.sock"   # Where clients connect (unix socket)
DOCKER_DIR="/var/lib/docker"                     # Docker data-root inside this container
DOCKER_STARTUP_TIMEOUT="300"                     # Seconds to wait for dockerd to become ready
DOCKER_PIDFILE="/var/run/docker.pid"             # PID file for dockerd
DOCKER_LOG="/var/log/dockerd.log"                # Log file for dockerd

# Directory + files for dns + http logger
MYLOG_DIR="$DATA_PATH/KC2/myservices"
DNS_CONF="/etc/dnsmasq.d/99-all-respond.conf"
DNS_LOG="${MYLOG_DIR}/dnsmasq.log"
HTTP_LOG="${MYLOG_DIR}/8080_requests.log"
SERVER_SCRIPT="/usr/local/bin/server_8080.py"

log() { printf '%s %s\n' "$(date -Is)" "$*"; }

# Clean previous results
rm -rf $DATA_PATH/*

# helper: detect container IPv4 (first non-loopback)
detect_container_ip() {
  local ip
  # prefer 'ip' tool
  if command -v ip >/dev/null 2>&1; then
    ip=$(ip -4 addr show scope global | awk '/inet/ {print $2}' | cut -d/ -f1 | head -n1 || true)
  fi
  if [ -z "${ip:-}" ] && command -v hostname >/dev/null 2>&1; then
    ip=$(hostname -I 2>/dev/null | awk '{print $1}' || true)
  fi
  ip=${ip:-127.0.0.1}
  printf '%s' "$ip"
}

# Setup dnsmasq conf that resolves EVERYTHING to this container IP and enables query logging.
setup_dnsmasq() {
  local container_ip
  container_ip=$(detect_container_ip)
  log "Detected container IP: $container_ip (for dnsmasq wildcard answer)"

  # Make sure /etc/dnsmasq.d exists
  mkdir -p /etc/dnsmasq.d

  cat > "$DNS_CONF" <<EOF
# Respond to every query with the container IP
address=/#/${container_ip}

# Log queries and write to file via syslog path (dnsmasq will use log-facility)
log-queries
log-facility=${DNS_LOG}

# Avoid reading /etc/hosts so we always return the container IP
no-hosts
EOF

  # Ensure dnsmasq run dir exists
  mkdir -p /var/run/dnsmasq
}

# Start dnsmasq in background and record PID in DNS_PID variable
start_dnsmasq() {
  if ! command -v dnsmasq >/dev/null 2>&1; then
    log "dnsmasq not installed; skipping DNS responder startup."
    return 0
  fi

  # If an existing dnsmasq is running, try to reuse / don't double-start
  if pgrep -x dnsmasq >/dev/null 2>&1; then
    log "dnsmasq already running on this host/container; skipping new start."
    return 0
  fi

  log "Starting dnsmasq (wildcard -> container IP); logs -> ${DNS_LOG}"
  # start dnsmasq with our conf dir. run in background and redirect stdout/stderr to dns log for extra info
  # --no-resolv avoids reading /etc/resolv.conf (we respond everything locally)
  dnsmasq --no-resolv --conf-dir=/etc/dnsmasq.d >>"${DNS_LOG}" 2>&1 &
  DNS_PID=$!
  log "dnsmasq started (pid=${DNS_PID})"
}

# Start our HTTP logger server (only when DOCKER_DAEMON = 1). Runs in background.
start_http_logger() {
  # only start if script exists and is executable
  if [ ! -x "${SERVER_SCRIPT}" ]; then
    log "HTTP logger script not found or not executable at ${SERVER_SCRIPT}; skipping 8080 server."
    return 0
  fi

  log "Starting HTTP logger (port 8080) -> logs ${HTTP_LOG}"
  # Start python server in background; it should implement logging of complete requests.
  # Use nohup to avoid SIGHUP killing it when this script exits/execs.
  nohup python3 "${SERVER_SCRIPT}" --bind 0.0.0.0 --port 8080 --logfile "${HTTP_LOG}" >>"${HTTP_LOG}" 2>&1 &
  HTTP_PID=$!
  log "HTTP logger started (pid=${HTTP_PID})"
}

# Cleanup helper (best-effort)
cleanup_before_exit() {
  log "Entrypoint cleanup: shutting down helper background processes..."
  if [ -n "${DNS_PID:-}" ] && kill -0 "$DNS_PID" 2>/dev/null; then
    log "Stopping dnsmasq (pid=${DNS_PID})"
    kill "$DNS_PID" || true
  fi
  if [ -n "${HTTP_PID:-}" ] && kill -0 "$HTTP_PID" 2>/dev/null; then
    log "Stopping http logger (pid=${HTTP_PID})"
    kill "$HTTP_PID" || true
  fi
}

# --- Existing Docker daemon logic (unchanged, only minor logging integration) ---
if [[ "${DOCKER_DAEMON:-0}" == "1" ]]; then

  # Ensure basic log dir exists and sane perms (and ensure cleanup)
  mkdir -p "$MYLOG_DIR" "$(dirname "$DOCKER_LOG")" /var/run
  touch "$DNS_LOG" "$HTTP_LOG" || true
  chmod 0644 "$DNS_LOG" "$HTTP_LOG" || true
  trap cleanup_before_exit INT TERM

  # --- Setup dnsmasq unconditionally (so DNS responder + logging are always present) ---
  setup_dnsmasq
  start_dnsmasq

  # Ensure run/data directories exist
  mkdir -p /var/run "$DOCKER_DIR"
  # Prepare log file with sane permissions (tail-able)
  touch "$DOCKER_LOG" && chmod 0644 "$DOCKER_LOG" || true

  # If a PID file exists, verify the process is actually running.
  # If not running, remove the stale PID so dockerd can start.
  if [[ -f "$DOCKER_PIDFILE" ]]; then
    pid="$(cat "$DOCKER_PIDFILE" 2>/dev/null || true)"
    if [[ -n "${pid:-}" ]] && kill -0 "$pid" 2>/dev/null; then
      log "Existing dockerd process detected (pid=$pid); assuming daemon is already running."
    else
      log "Stale PID file found at $DOCKER_PIDFILE (pid=$pid not running). Removing it."
      rm -f "$DOCKER_PIDFILE"
    fi
  fi

  # Start dockerd in the background.
  # NOTE:
  # - storage-driver: 'vfs' is slower but works without privileged mode; use 'overlay2' if privileged is available.
  # - --insecure-registry is optional; supply HOSTREGISTRY to enable.
  log "Starting dockerd (data-root=$DOCKER_DIR, host=$DOCKER_HOST_UNIX, driver=vfs)..."
  dockerd \
    --data-root="$DOCKER_DIR" \
    --host="$DOCKER_HOST_UNIX" \
    --storage-driver=vfs \
    --insecure-registry "$HOSTREGISTRY" \
    >>"$DOCKER_LOG" 2>&1 &

  DOCKERD_PID=$!
  log "dockerd launched with pid $DOCKERD_PID. Waiting for Docker socket to become ready..."

  # Start HTTP logger now that DOCKER_DAEMON=1 (we run it in background so exec "$@" remains main process)
  start_http_logger

  # Poll for 'docker info' until the daemon is responsive or timeout expires.
  elapsed=0
  interval=1
  until docker --host="$DOCKER_HOST_UNIX" info >/dev/null 2>&1; do
    sleep "$interval"
    elapsed=$((elapsed + interval))

    # If dockerd crashed during startup, print last logs and exit early.
    if ! kill -0 "$DOCKERD_PID" 2>/dev/null; then
      log "dockerd process $DOCKERD_PID exited prematurely. Recent logs:"
      tail -n 200 "$DOCKER_LOG" || true
      cleanup_before_exit
      exit 1
    fi

    # Enforce a hard timeout to avoid hanging forever.
    if [[ "$elapsed" -ge "$DOCKER_STARTUP_TIMEOUT" ]]; then
      log "ERROR: dockerd did not become ready within ${DOCKER_STARTUP_TIMEOUT}s. Recent logs:"
      tail -n 400 "$DOCKER_LOG" || true
      cleanup_before_exit
      exit 1
    fi
  done

  log "==> Docker daemon is ready (waited ${elapsed}s)."
fi

# Export DOCKER_HOST so child processes (CMD) use the expected socket.
export DOCKER_HOST="${DOCKER_HOST_UNIX}"

# Hand off to the container's main process (never returns).
# Note: dnsmasq and http logger (if started) run in background.
exec "$@"
