#!/usr/bin/env bash
set -euo pipefail

FILE_IP="/tmp/iphost"
KEY_PATH="$DATA_PATH/KC5/ssh/ssh-key"
SSH_OPTS=(
  -p 2222
  -o StrictHostKeyChecking=accept-new
  -o BatchMode=yes
  -o ConnectTimeout=5
)

timestamp() { date +"%Y-%m-%d %H:%M:%S"; }

list_node_ips() {
  if [[ ! -f "$FILE_IP" ]]; then
    echo "Error: file '$FILE_IP' not found" >&2
    return 1
  fi

  echo ">> Recovering IP list (file: $FILE_IP)..." >&2
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

# Wait until the node is reachable via SSH
wait_for_ssh() {
  local ip="$1"
  local timeout_sec="${2:-300}"   # default: 5 minutes
  local start now elapsed sleep_s=2 attempt=0

  start=$(date +%s)

  while : ; do
    attempt=$((attempt+1))
    # Simple SSH probe that runs `true` remotely
    if ssh "${SSH_OPTS[@]}" -i "$KEY_PATH" "root@$ip" true </dev/null 2>/dev/null; then
      echo "$(timestamp) >> $ip: SSH is ready (attempt $attempt)"
      return 0
    fi

    now=$(date +%s)
    elapsed=$((now - start))
    if (( elapsed >= timeout_sec )); then
      echo "$(timestamp) >> $ip: SSH not reachable after ${elapsed}s (attempts: $attempt)" >&2
      return 1
    fi

    echo "$(timestamp) >> $ip: SSH not ready yet (attempt $attempt). Retrying..."
    sleep "$sleep_s"
    # Exponential backoff up to 20s
    (( sleep_s < 20 )) && sleep_s=$(( sleep_s * 2 ))
  done
}

mapfile -t nodes < <(list_node_ips | sed '/^$/d')
if [[ "${#nodes[@]}" -eq 0 ]]; then
  echo "No node found"; exit 1
fi

echo ">> Nodes found (${#nodes[@]}): ${nodes[*]}"

for n in "${nodes[@]}"; do
  # Wait for SSH to become reachable
  if ! wait_for_ssh "$n" 600; then
    echo "SKIP: $n is not reachable, moving on" >&2
    continue
  fi

  # Run the remote job only after SSH is ready
  echo "$(timestamp) >> Starting analysis on $n"
  if ssh "${SSH_OPTS[@]}" -i "$KEY_PATH" "root@$n" 'bash -s --' -- "0" \
       < /opt/caldera/KC4/container-collection.sh \
       > "$DATA_PATH/KC5/container-collection-$n.log" 2>&1
  then
    echo "Analysis completed for $n"
  else
    rc=$?
    echo "$(timestamp) >> ERROR: analysis on $n failed (rc=$rc) — check $DATA_PATH/KC5/container-collection-$n.log" >&2
  fi
done
