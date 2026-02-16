#!/usr/bin/env bash
set -euo pipefail

SCRIPT_START_EPOCH="$(date +%s)"
echo "[$(date '+%Y-%m-%d %H:%M:%S %Z')] Script started"

on_exit() {
  local exit_code=$?
  local script_end_epoch end_ts duration

  script_end_epoch="$(date +%s)"
  end_ts="$(date '+%Y-%m-%d %H:%M:%S %Z')"
  duration=$((script_end_epoch - SCRIPT_START_EPOCH))

  if [[ $exit_code -eq 0 ]]; then
    echo "[$end_ts] Pipeline completed (duration: ${duration}s)"
  else
    echo "[$end_ts] Script failed with exit code ${exit_code} (duration: ${duration}s)" >&2
  fi
}
trap on_exit EXIT

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
IMAGE_VERSION=2.0.2
export PROJECT_ROOT IMAGE_VERSION

# === Load variables from config file ===
ENV_FILE="${PROJECT_ROOT}/configuration.conf"
if [[ ! -f "$ENV_FILE" ]]; then
  err "Configuration file not found: $ENV_FILE" >&2
  exit 1
fi

# Parse $ENV_FILE and export KEY=VALUE pairs (supports <<HEREDOC, 'single', "double")
while IFS= read -r line || [[ -n "$line" ]]; do
  # Skip blank lines and comments (optionally indented)
  [[ -z "${line//[[:space:]]/}" ]] && continue
  [[ "${line#"${line%%[![:space:]]*}"}" =~ ^# ]] && continue

  # ---- Here-doc style: KEY<<MARKER ----
  if [[ "$line" == *'<<'* ]]; then
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
    value="${value%$'\n'}"   # remove trailing newline
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

    # helper: safely assign and export without word-splitting
    _export_kv() { local k="$1" v="$2"; printf -v "$k" '%s' "$v"; export "$k"; }

    # Single quotes → literal (no escape processing)
    if [[ "$raw" =~ ^\'(.*)\'$ ]]; then
      _export_kv "$key" "${BASH_REMATCH[1]}"
      continue
    fi

    # Double quotes → first unescape \" → ", then decode \n, \t, \\
    if [[ "$raw" =~ ^\"(.*)\"$ ]]; then
      inner="${BASH_REMATCH[1]}"
      # 1) replace \" → "
      inner="${inner//\\\"/\"}"
      # 2) interpret other escapes (\n, \t, \\ etc.)
      value="$(printf '%b' "$inner")"
      _export_kv "$key" "$value"
      continue
    fi

    # Unquoted → take as-is
    _export_kv "$key" "$raw"
    continue
  fi

  # ---- Unrecognized line ----
  echo "Warning: ignoring unrecognized line: $line" >&2
done < "$ENV_FILE"

source "${PROJECT_ROOT}/pb/scripts/00_ensure_deps.sh"
source "${PROJECT_ROOT}/pb/scripts/01_kind_cluster.sh"
source "${PROJECT_ROOT}/pb/scripts/02_setup_underlay.sh"
source "${PROJECT_ROOT}/pb/scripts/03_run_underlay.sh"
source "${PROJECT_ROOT}/pb/scripts/04_build_deploy.sh"
source "${PROJECT_ROOT}/pb/scripts/05_setup_k8s.sh"

on_exit
