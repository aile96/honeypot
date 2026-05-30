#!/usr/bin/env bash

if [ -f ${ATTACKER_LIB_DIR:-/opt/attacker-lib}/common/require-tools.sh ]; then
  # Prefer the shared helper when the script runs inside the attacker image.
  source ${ATTACKER_LIB_DIR:-/opt/attacker-lib}/common/require-tools.sh
else
  # Remote stdin execution may not have the helper file available.
  require_tools() {
    local missing=()
    local tool
    for tool in "$@"; do
      command -v "$tool" >/dev/null 2>&1 || missing+=("$tool")
    done
    if (( ${#missing[@]} > 0 )); then
      echo "Missing required tools: ${missing[*]}" >&2
      exit 1
    fi
  }
fi

OUTDIR="${1:-$DATA_PATH/KC2}"
RMFILE="${2:-0}"

require_tools nmap ip awk getent

# Show the revealed network
NETWORK=$(ip -o -4 addr show | awk 'NR>1{print $4}' | awk -F. 'NF==4{print $1"."$2"."$3".0/24"; exit}')
echo "[KC2-202] scanning local network ${NETWORK}"
mkdir -p $OUTDIR

# Network scan (/23 to be faster)
nmap -sn -T4 "$NETWORK" -oG - \
  | awk '/Up$/{print $2}' \
  | while read -r ip; do
      host=$(getent hosts "$ip" | awk '{print $2}')
      if [ -z "$host" ]; then
        host="UNKNOWN"
      fi
      echo "$ip - $host"
    done > $OUTDIR/iphost

if [ ! -s "$OUTDIR/iphost" ]; then
  echo "No hosts discovered in $NETWORK" >&2
  exit 1
fi

HOST_COUNT="$(wc -l < "$OUTDIR/iphost" | tr -d ' ')"
echo "[KC2-202] discovered ${HOST_COUNT} hosts; inventory saved in $OUTDIR/iphost"

if [ "${RMFILE}" -eq 1 ]; then
  rm -f "$OUTDIR/iphost"
fi
