#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# === Carica variabili da config/config.sh (ignora commenti e righe vuote) ===
ENV_FILE="${PROJECT_ROOT}/project.env"
if [[ ! -f "$ENV_FILE" ]]; then
  echo "File di configurazione non trovato: $ENV_FILE" >&2
  exit 1
fi

while IFS= read -r line; do
  [[ -z "$line" ]] && continue         # salta righe vuote
  [[ "$line" =~ ^# ]] && continue      # salta commenti
  export "$line"
done < "$ENV_FILE"

remove_registry_hosts() {
  local host="registry"
  local hosts="/etc/hosts"

  if ! grep -Eq "^[[:space:]]*[0-9a-fA-F:.]+[[:space:]]+.*\b${host}\b" "$hosts"; then
    echo "Nessuna entry '${host}' trovata in ${hosts}"
    return 0
  fi

  echo "Rimuovo '${host}' da ${hosts}..."
  local tmp
  tmp="$(mktemp)"

  # Copia di backup
  sudo cp "$hosts" "${hosts}.bak.$(date +%s)"

  # Rimuove il token 'registry' preservando eventuali altri alias sulla stessa riga
  awk -v host="registry" '
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

  echo "Rimosso '${host}' da ${hosts}..."
}

kind delete cluster --name $CLUSTER_NAME
docker container rm -f $CALDERA_SERVER $CALDERA_ATTACKER
#remove_registry_hosts
#docker container rm -f $REGISTRY_NAME
