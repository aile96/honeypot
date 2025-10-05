#!/usr/bin/env bash
set -euo pipefail

API_SERVER="${API_SERVER:-https://kind-cluster-control-plane:6443}"
MODE="${MODE:-ready}"                    # ready | notready
BASE="/tmp/KCData/KC5"
PID_DIR="$BASE/pids"; LOG_DIR="$BASE/logs"
PID_LIST="${PID_LIST:-$BASE/state_pids}"

mkdir -p "$PID_DIR" "$LOG_DIR"
: > "$PID_LIST"

node_from_pem() {
  openssl x509 -in "$1" -noout -subject 2>/dev/null \
    | sed -n 's/^subject=.*CN *= *system:node:\([^,/]*\).*/\1/p' | head -n1
}

for pem in "$BASE"/*.pem; do
  [[ -f "$pem" ]] || continue
  NODE="$(node_from_pem "$pem")"
  if [[ -z "$NODE" ]]; then
    echo "SKIP $pem (CN non è system:node:*)" >&2
    continue
  fi

  PID_FILE="$PID_DIR/$NODE.pid"
  LOG_FILE="$LOG_DIR/$NODE.log"

  # se già in esecuzione, salta
  if [[ -f "$PID_FILE" ]] && ps -p "$(cat "$PID_FILE")" > /dev/null 2>&1; then
    echo "Già attivo: $NODE (PID $(cat "$PID_FILE"))"
    echo "$(cat "$PID_FILE") $pem" >> "$PID_LIST"
    continue
  fi

  echo "Avvio updater per $NODE con $pem (MODE=$MODE)"
  # stacca dal terminale e logga su file
  setsid env \
    API_SERVER="$API_SERVER" \
    CERT_PATH="$pem" \
    MODE="$MODE" \
    PID_FILE="$PID_FILE" \
    bash /opt/caldera/common/api-loop.sh \
      >"$LOG_FILE" 2>&1 < /dev/null &

  pid=$!
  echo "$pid $pem" | tee -a "$PID_LIST"
done

echo "PIDs in $PID_LIST"
echo "PID per nodo in $PID_DIR/*.pid"
echo "Log per nodo in $LOG_DIR/*.log"
