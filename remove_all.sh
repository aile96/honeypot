#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# === Load variables from config file ===
ENV_FILE="${PROJECT_ROOT}/skaffold.env"
if [[ ! -f "$ENV_FILE" ]]; then
  echo "Configuration file not found: $ENV_FILE" >&2
  exit 1
fi

while IFS= read -r line; do
  [[ -z "$line" ]] && continue         # skip empty lines
  [[ "$line" =~ ^# ]] && continue      # skip comments
  export "$line"
done < "$ENV_FILE"

remove_registry() {
  local host="${1:-registry}"         # 1° arg: host to remove (default: registry)
  local hosts="${2:-/etc/hosts}"      # 2° arg: path to hosts file (default: /etc/hosts)

  docker rm -f $1

  # Looking if exists at least one entry with host
  if ! grep -Eq "^[[:space:]]*[0-9a-fA-F:.]+[[:space:]]+.*\b${host}\b" "$hosts"; then
    echo "No entry '${host}' found in ${hosts}"
    return 0
  fi

  echo "Removing '${host}' from ${hosts}..."
  local tmp
  tmp="$(mktemp)"

  # Backup
  sudo cp "$hosts" "${hosts}.bak.$(date +%s)"

  # Removing $host saving other alias on the same line
  awk -v host="$host" '
    /^[[:space:]]*#/ { print; next }             # leaving comments
    NF == 0 { print; next }                       # leaving empty lines
    {
      ip=$1; out=$1; removed=0
      for (i=2; i<=NF; i++) {
        if ($i != host) out = out FS $i
        else removed=1
      }
      if (removed && out == ip) next             # if not other host, remove the line
      print out
    }
  ' "$hosts" > "$tmp"

  sudo install -m 0644 "$tmp" "$hosts"
  rm -f "$tmp"

  echo "Deleted '${host}' from ${hosts}."
}

echo "Deleting cluster k8s" && skaffold delete && echo "Deletion completed" || echo "Deletion ended with errors"
echo "Deleting dockers (no kind)" && docker compose -f pb/docker/docker-compose.yml down && echo "Deletion completed" || echo "Deletion ended with errors"
echo "Deleting kind" && kind delete cluster --name $CLUSTER_NAME && docker network rm kind && echo "Deletion completed" || echo "Deletion ended with errors"
