#!/bin/bash

# Mostra la rete rilevata
echo "NETWORK: $(ip -o -4 addr show | awk 'NR>1{print $4}' | awk -F. 'NF==4{print $1"."$2".0.0/23"; exit}')"

# Scansione della rete (qui uso /16, modifica se vuoi più stretto)
nmap -sn -T4 "$(ip -o -4 addr show | awk 'NR>1{print $4}' | awk -F. 'NF==4{print $1"."$2".0.0/23"; exit}')" -oG - \
  | awk '/Up$/{print $2}' \
  | while read -r ip; do
      host=$(getent hosts "$ip" | awk '{print $2}')
      if [ -z "$host" ]; then
        host="UNKNOWN"
      fi
      echo "$ip - $host"
    done > /tmp/iphost

cat /tmp/iphost