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

echo "Deleting dockers (no kind)" && docker rm -f $CALDERA_SERVER $ATTACKER $CALDERA_CONTROLLER $PROXY $REGISTRY_NAME load-generator samba-pv && echo "Deletion completed" || echo "Deletion ended with errors"
echo "Removing helm in k8s" && \
helm uninstall csi-driver-smb --namespace kube-system 2>/dev/null || true; \
helm uninstall honeypot-additions 2>/dev/null || true; \
helm uninstall honeypot-telemetry --namespace "${MEM_NAMESPACE:-mem}" 2>/dev/null || true; \
helm uninstall honeypot-astronomy-shop 2>/dev/null || true; \
helm uninstall metallb --namespace metallb-system 2>/dev/null || true
echo "Deletion completed"
kubectl delete -n kube-system daemonset.apps/node-agent || true
echo "Deleting kind" && kind delete cluster && docker network rm kind && echo "Deletion completed" || echo "Deletion ended with errors"
echo "Deleting minikube" && minikube delete && docker network rm minikube && echo "Deletion completed" || echo "Deletion ended with errors"