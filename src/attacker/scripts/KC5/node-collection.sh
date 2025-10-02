#!/usr/bin/env bash
set -euo pipefail

FILE_IP="$DATA_PATH/KC5/iphost"
KEY_PATH="$DATA_PATH/KC5/ssh/ssh-key"

list_node_ip() {
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
    ssh -p 2222 -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o IdentitiesOnly=yes -i $KEY_PATH root@$n \
        '/usr/bin/env bash -s' < /opt/caldera/KC4/container-analysis.sh > $DATA_PATH/KC5/container-analysis-$n.log 2>&1
    echo "Analysis completed for $n"
done
