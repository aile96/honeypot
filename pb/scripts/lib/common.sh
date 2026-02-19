#!/usr/bin/env bash

# Guard against double sourcing.
if [[ -n "${HONEYPOT_COMMON_LIB_LOADED:-}" ]]; then
  return 0
fi
HONEYPOT_COMMON_LIB_LOADED=1

log()  { printf "\033[1;36m[INFO]\033[0m %s\n" "$*"; }
warn() { printf "\033[1;33m[WARN]\033[0m %s\n" "$*" >&2; }
err()  { printf "\033[1;31m[ERR ]\033[0m %s\n" "$*" >&2; }

# Return when sourced, exit when executed directly.
return_or_exit() {
  local code="${1:-0}"
  local caller_file=""
  local i

  for ((i=1; i<${#BASH_SOURCE[@]}; i++)); do
    if [[ "${BASH_SOURCE[$i]}" != "${BASH_SOURCE[0]}" ]]; then
      caller_file="${BASH_SOURCE[$i]}"
      break
    fi
  done

  if [[ -n "$caller_file" && "$caller_file" != "$0" ]]; then
    return "$code"
  fi
  exit "$code"
}

# Append a trap handler without clobbering handlers added through trap_add.
trap_add() {
  local new_cmd="${1:-}"
  local signal="${2:-EXIT}"
  local var_name="HONEYPOT_TRAP_CMDS_${signal}"
  local existing="${!var_name:-}"
  local line

  [[ -n "$new_cmd" ]] || return 0

  while IFS= read -r line; do
    [[ -n "${line}" ]] || continue
    if [[ "${line}" == "${new_cmd}" ]]; then
      return 0
    fi
  done <<< "${existing}"

  if [[ -n "${existing}" ]]; then
    existing+=$'\n'
  fi
  existing+="${new_cmd}"

  printf -v "${var_name}" '%s' "${existing}"
  trap -- "${existing}" "${signal}"
}

die()  { printf "\033[1;31m[ERROR]\033[0m %s\n" "$*" >&2; return_or_exit 1; }
fail() { printf "\033[1;31m[FAIL]\033[0m %s\n" "$*" >&2; return_or_exit 1; }
req()  { command -v "$1" >/dev/null 2>&1 || die "Required command not found: $1"; }

is_true()  { [[ "${1,,}" == "true" ]]; }
is_false() { [[ "${1,,}" == "false" ]]; }

# Retry a command with fixed delay between attempts.
# Usage: retry_cmd <attempts> <delay_seconds> <label> <cmd> [args...]
retry_cmd() {
  local attempts="${1:-}"
  local delay_seconds="${2:-}"
  local label="${3:-command}"
  shift 3 || true

  [[ "${attempts}" =~ ^[0-9]+$ ]] || die "retry_cmd: attempts must be an integer >= 1 (got '${attempts}')."
  (( attempts >= 1 )) || die "retry_cmd: attempts must be >= 1."
  [[ "${delay_seconds}" =~ ^[0-9]+$ ]] || die "retry_cmd: delay_seconds must be an integer >= 0 (got '${delay_seconds}')."
  (( $# > 0 )) || die "retry_cmd: missing command."

  local attempt=1
  local rc=0
  while true; do
    if "$@"; then
      rc=0
      if (( attempt > 1 )); then
        log "${label} succeeded on attempt ${attempt}/${attempts}."
      fi
      return 0
    else
      rc=$?
    fi

    if (( attempt >= attempts )); then
      err "${label} failed on attempt ${attempt}/${attempts} (exit=${rc})."
      return "${rc}"
    fi

    warn "${label} failed on attempt ${attempt}/${attempts} (exit=${rc}); retrying in ${delay_seconds}s..."
    if (( delay_seconds > 0 )); then
      sleep "${delay_seconds}"
    fi
    attempt=$((attempt + 1))
  done
}

normalize_bool_var() {
  local var_name="$1"
  local value="${!var_name:-}"

  case "${value,,}" in
    true|false)
      printf -v "$var_name" '%s' "${value,,}"
      ;;
    *)
      die "Invalid boolean for ${var_name}: '${value}' (expected true|false)."
      ;;
  esac
}

require_port_var() {
  local var_name="$1"
  local value="${!var_name:-}"

  [[ "$value" =~ ^[0-9]+$ ]] || die "Invalid port for ${var_name}: '${value}' (expected integer 1-65535)."
  (( value >= 1 && value <= 65535 )) || die "Invalid port for ${var_name}: '${value}' (expected range 1-65535)."
}

refresh_docker_ip_cache() {
  local -a cids=()
  local name nets ips tip

  declare -gA HONEYPOT_DOCKER_IP_CACHE=()
  HONEYPOT_DOCKER_IP_CACHE_READY=0

  mapfile -t cids < <(docker ps -q 2>/dev/null || true)
  if (( ${#cids[@]} == 0 )); then
    HONEYPOT_DOCKER_IP_CACHE_READY=1
    return 0
  fi

  while IFS='|' read -r name nets ips; do
    name="${name#/}"
    nets="$(printf '%s' "${nets:-}" | xargs 2>/dev/null || true)"
    ips="$(printf '%s' "${ips:-}" | xargs 2>/dev/null || true)"
    [[ -n "${name}" && -n "${ips}" ]] || continue

    for tip in ${ips}; do
      [[ -n "${tip}" ]] || continue
      HONEYPOT_DOCKER_IP_CACHE["${tip}"]="${name};${nets}"
    done
  done < <(
    docker inspect \
      -f '{{.Name}}|{{range $k,$v := .NetworkSettings.Networks}}{{$k}} {{end}}|{{range $k,$v := .NetworkSettings.Networks}}{{$v.IPAddress}} {{end}}' \
      "${cids[@]}" 2>/dev/null || true
  )

  HONEYPOT_DOCKER_IP_CACHE_READY=1
}

find_docker_container_by_ip() {
  local ip="$1"
  local pair=""

  [[ -n "${ip}" ]] || return 1

  if [[ "${HONEYPOT_DOCKER_IP_CACHE_READY:-0}" != "1" ]]; then
    refresh_docker_ip_cache
  fi

  pair="${HONEYPOT_DOCKER_IP_CACHE[${ip}]:-}"
  if [[ -n "${pair}" ]]; then
    printf '%s' "${pair}"
    return 0
  fi

  # One refresh retry handles containers created after the cache was primed.
  refresh_docker_ip_cache
  pair="${HONEYPOT_DOCKER_IP_CACHE[${ip}]:-}"
  if [[ -n "${pair}" ]]; then
    printf '%s' "${pair}"
    return 0
  fi

  return 1
}
