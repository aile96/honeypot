#!/usr/bin/env bash
set -euo pipefail

BASE="$DATA_PATH/KC5"
FILE_IP="$BASE/iphost"
KEY_PATH="$BASE/ssh/ssh-key"
PID_LIST="$BASE/pids"
WAITING_TIME="120"

echo "DOS for all services of the cluster for $WAITING_TIME s"
sleep $WAITING_TIME

list_node_hostnames() {
  if [[ ! -f "$FILE_IP" ]]; then
    echo "Errore: file '$FILE_IP' non trovato" >&2
    return 1
  fi

  echo ">> Recupero lista IPs (da file: $FILE_IP)..." >&2

  # raccogliamo e stampiamo alla fine per poter fare sort -u
  grep -E '[-]' "$FILE_IP" \
   | cut -d'-' -f2- \
   | sed -E 's/^[[:space:]]+|[[:space:]\r]+$//g' \
   | grep worker \
   | cut -d'.' -f1 \
   | sort -u
}

mapfile -t nodes < <(list_node_hostnames | sed '/^$/d')
if [[ "${#nodes[@]}" -eq 0 ]]; then
  echo "Nessun nodo trovato."; exit 1
fi
echo ">> Nodi trovati (${#nodes[@]}): ${nodes[*]}"
for n in "${nodes[@]}"; do
  ssh -p 2222 -i $KEY_PATH root@$n kill -CONT "$(ssh -p 2222 -i $KEY_PATH root@$n pidof kubelet)"
  echo "Remove everything for $n"
done

/opt/caldera/common/remove-pids.sh $PID_LIST