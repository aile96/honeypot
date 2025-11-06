#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# === Load variables from config file ===
ENV_FILE="${PROJECT_ROOT}/configuration.conf"
if [[ ! -f "$ENV_FILE" ]]; then
  echo "Configuration file not found: $ENV_FILE" >&2
  exit 1
fi

while IFS= read -r line || [[ -n "$line" ]]; do
  # Skip blank lines and comments (optionally indented)
  [[ -z "${line//[[:space:]]/}" ]] && continue
  [[ "${line#"${line%%[![:space:]]*}"}" =~ ^# ]] && continue

  # ---- Here-doc style: KEY<<MARKER ----
  if [[ "$line" == *'<<'* ]]; then
    # split at the first '<<'
    lhs="${line%%<<*}"
    rhs="${line#*<<}"

    # trim spaces around key and marker
    key="${lhs#"${lhs%%[![:space:]]*}"}"; key="${key%"${key##*[![:space:]]}"}"
    marker="${rhs#"${rhs%%[![:space:]]*}"}"; marker="${marker%"${marker##*[![:space:]]}"}"

    # strip optional single/double quotes around marker
    case "$marker" in
      \"*\") marker="${marker#\"}"; marker="${marker%\"}" ;;
      \'*\') marker="${marker#\'}"; marker="${marker%\'}" ;;
    esac

    value=''
    while IFS= read -r docline; do
      [[ "$docline" == "$marker" ]] && break
      value+="${docline}"$'\n'
    done
    value="${value%$'\n'}"   # remove trailing newline (optional)
    export "$key=$value"
    continue
  fi

  # ---- Normal KEY=VALUE line ----
  if [[ "$line" =~ ^([A-Za-z_][A-Za-z0-9_]*)=(.*)$ ]]; then
    key="${BASH_REMATCH[1]}"
    raw="${BASH_REMATCH[2]}"

    # trim surrounding spaces
    raw="${raw#"${raw%%[![:space:]]*}"}"
    raw="${raw%"${raw##*[![:space:]]}"}"

    # Single quotes → literal
    if [[ "$raw" =~ ^\'(.*)\'$ ]]; then
      value="${BASH_REMATCH[1]}"
      export "$key=$value"
      continue
    fi

    # Double quotes → interpret \n, \t, etc.
    if [[ "$raw" =~ ^\"(.*)\"$ ]]; then
      inner="${BASH_REMATCH[1]}"
      value="$(printf '%b' "$inner")"
      export "$key=$value"
      continue
    fi

    # Unquoted → take as-is
    value="$raw"
    export "$key=$value"
    continue
  fi

  # ---- Unrecognized line ----
  echo "Warning: ignoring unrecognized line: $line" >&2
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
echo "Deleting kind" && kind delete cluster && docker network rm kind && echo "Deletion completed" || echo "Deletion ended with errors"
echo "Deleting minikube" && minikube delete && docker network rm minikube && echo "Deletion completed" || echo "Deletion ended with errors"