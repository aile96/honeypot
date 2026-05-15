#!/usr/bin/env bash
set -euo pipefail

BASE="$DATA_PATH/KC5"
FILE_IP="/tmp/iphost"
KEY_PATH="$BASE/ssh/ssh-key"
PID_LIST="$BASE/pids"
WAITING_TIME="10"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NODE_IP_HELPER="${SCRIPT_DIR}/../common/list-node-ips.sh"

[[ -f "${NODE_IP_HELPER}" ]] || { echo "Missing helper: ${NODE_IP_HELPER}" >&2; exit 1; }
# shellcheck source=../common/list-node-ips.sh
source "${NODE_IP_HELPER}"

echo "DOS for all services of the cluster for $WAITING_TIME s"
sleep "${WAITING_TIME}"

mapfile -t nodes < <(list_worker_node_ips "$FILE_IP" | sed '/^$/d')
if [[ "${#nodes[@]}" -eq 0 ]]; then
  echo "No node found"; exit 1
fi
echo ">> Nodes found (${#nodes[@]}): ${nodes[*]}"
for n in "${nodes[@]}"; do
  ssh -p 2222 -i "$KEY_PATH" "root@$n" kill -CONT "$(ssh -p 2222 -i "$KEY_PATH" "root@$n" pidof kubelet)"
  echo "Remove everything for $n"
done

/opt/caldera/common/remove-pids.sh "$PID_LIST"
