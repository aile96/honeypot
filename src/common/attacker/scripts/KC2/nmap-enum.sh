#!/usr/bin/env bash

source /opt/caldera/common/require-tools.sh

OUTDIR="${1:-$DATA_PATH/KC2}"
RMFILE="${2:-0}"

require_tools nmap ip awk getent

# Show the revealed network
NETWORK=$(ip -o -4 addr show | awk 'NR>1{print $4}' | awk -F. 'NF==4{print $1"."$2"."$3".0/24"; exit}')
echo "#NETWORK: $NETWORK"
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

cat $OUTDIR/iphost

if [ "${RMFILE}" -eq 1 ]; then
  rm -f "$OUTDIR/iphost"
fi
