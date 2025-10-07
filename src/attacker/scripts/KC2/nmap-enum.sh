#!/usr/bin/env bash

OUTDIR="${1:-$DATA_PATH/KC2}"
RMFILE="${2:-0}"

install_deps() {
  # Installa solo se nmap non è presente
  if command -v nmap >/dev/null 2>&1; then
    return
  fi

  if command -v apt-get >/dev/null 2>&1; then
    env DEBIAN_FRONTEND=noninteractive apt-get update -y >/dev/null 2>&1
    env DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
      nmap iproute2 >/dev/null 2>&1
    # getent è in libc-bin (di solito già presente su Debian/Ubuntu)

  elif command -v apk >/dev/null 2>&1; then
    apk update >/dev/null 2>&1
    # musl-utils fornisce `getent`; iproute2 fornisce `ip`
    apk add --no-cache nmap iproute2 musl-utils >/dev/null 2>&1

  else
    echo "Package manager non supportato (serve apt-get o apk)" >&2
    exit 1
  fi
}

install_deps

# Mostra la rete rilevata
echo "#NETWORK: $(ip -o -4 addr show | awk 'NR>1{print $4}' | awk -F. 'NF==4{print $1"."$2".0.0/23"; exit}')"
mkdir -p $OUTDIR

# Scansione della rete (qui uso /16, modifica se vuoi più stretto)
nmap -sn -T4 "$(ip -o -4 addr show | awk 'NR>1{print $4}' | awk -F. 'NF==4{print $1"."$2".0.0/23"; exit}')" -oG - \
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