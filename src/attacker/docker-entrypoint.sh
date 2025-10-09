set -euo pipefail

DOCKER_HOST_UNIX="unix:///var/run/docker.sock"   # Where clients connect (unix socket)
DOCKER_DIR="/var/lib/docker"                           # Docker data-root inside this container
DOCKER_STARTUP_TIMEOUT="300"               # Seconds to wait for dockerd to become ready
DOCKER_PIDFILE="/var/run/docker.pid"               # PID file for dockerd
DOCKER_LOG="/var/log/dockerd.log"                      # Log file for dockerd

log() { printf '%s %s\n' "$(date -Is)" "$*"; }

if [[ "${DOCKER_DAEMON:-0}" == "1" ]]; then
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
      exit 1
    fi

    # Enforce a hard timeout to avoid hanging forever.
    if [[ "$elapsed" -ge "$DOCKER_STARTUP_TIMEOUT" ]]; then
      log "ERROR: dockerd did not become ready within ${DOCKER_STARTUP_TIMEOUT}s. Recent logs:"
      tail -n 400 "$DOCKER_LOG" || true
      exit 1
    fi
  done

  log "==> Docker daemon is ready (waited ${elapsed}s)."
fi

# Export DOCKER_HOST so child processes (CMD) use the expected socket.
export DOCKER_HOST="${DOCKER_HOST_UNIX}"

# Hand off to the container's main process (never returns).
exec "$@"
