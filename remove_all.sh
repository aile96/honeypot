#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# === Carica variabili da config/config.sh (ignora commenti e righe vuote) ===
ENV_FILE="${PROJECT_ROOT}/skaffold.env"
if [[ ! -f "$ENV_FILE" ]]; then
  echo "File di configurazione non trovato: $ENV_FILE" >&2
  exit 1
fi

while IFS= read -r line; do
  [[ -z "$line" ]] && continue         # salta righe vuote
  [[ "$line" =~ ^# ]] && continue      # salta commenti
  export "$line"
done < "$ENV_FILE"

remove_registry() {
  local host="${1:-registry}"         # 1° arg: host da rimuovere (default: registry)
  local hosts="${2:-/etc/hosts}"      # 2° arg opzionale: path al file hosts (default: /etc/hosts)

  docker rm -f $1

  # Controlla se esiste almeno un'entry contenente l'host come token intero
  if ! grep -Eq "^[[:space:]]*[0-9a-fA-F:.]+[[:space:]]+.*\b${host}\b" "$hosts"; then
    echo "Nessuna entry '${host}' trovata in ${hosts}"
    return 0
  fi

  echo "Rimuovo '${host}' da ${hosts}..."
  local tmp
  tmp="$(mktemp)"

  # Backup
  sudo cp "$hosts" "${hosts}.bak.$(date +%s)"

  # Rimuove il token $host preservando eventuali altri alias sulla stessa riga
  awk -v host="$host" '
    /^[[:space:]]*#/ { print; next }             # lascia i commenti
    NF == 0 { print; next }                       # lascia le righe vuote
    {
      ip=$1; out=$1; removed=0
      for (i=2; i<=NF; i++) {
        if ($i != host) out = out FS $i
        else removed=1
      }
      if (removed && out == ip) next             # se non restano hostnames, elimina la riga
      print out
    }
  ' "$hosts" > "$tmp"

  sudo install -m 0644 "$tmp" "$hosts"
  rm -f "$tmp"

  echo "Rimosso '${host}' da ${hosts}."
}

echo "Rimozione cluster k8s" && skaffold delete && echo "Rimozione completata" || echo "Rimozione non andata a buon fine"
echo "Rimozione docker esclusi kind" && docker compose -f pb/docker/docker-compose.yml down && echo "Rimozione completata" || echo "Rimozione non andata a buon fine"
#echo "Rimozione kind" && kind delete cluster --name $CLUSTER_NAME && docker network rm kind && echo "Rimozione completata" || echo "Rimozione non andata a buon fine"
