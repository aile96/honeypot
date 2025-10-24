#!/usr/bin/env bash

OUTDIR="${1:-$DATA_PATH/KC2}"
RMFILE="${2:-0}"

install_deps() {
  # Install only if not present
  if command -v nmap >/dev/null 2>&1; then
    return
  fi

  if command -v apt-get >/dev/null 2>&1; then
    env DEBIAN_FRONTEND=noninteractive apt-get update -y >/dev/null 2>&1
    env DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
      nmap iproute2 mawk ca-certificates >/dev/null 2>&1

  elif command -v apk >/dev/null 2>&1; then
    apk update >/dev/null 2>&1
    apk add --no-cache nmap iproute2 musl-utils \
      gawk ca-certificates libc-utils >/dev/null 2>&1

  else
    echo "Package manager not supported (apt-get or apk)" >&2
    exit 1
  fi
}

install_deps

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