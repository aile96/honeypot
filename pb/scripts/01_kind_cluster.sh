#!/bin/bash

### === Funzioni di utilità ===
log() { printf "\033[1;36m[INFO]\033[0m %s\n" "$*"; }
warn() { printf "\033[1;33m[WARN]\033[0m %s\n" "$*"; }
err() { printf "\033[1;31m[ERR ]\033[0m %s\n" "$*" >&2; }
die()   { echo -e "[ERROR] $*" >&2; exit 1; }

ensure_registry_hosts() {
  local ip="127.0.0.1"
  local hosts="/etc/hosts"

  # serve sudo se non lanci come root
  if grep -Eq "^[[:space:]]*${ip//./\\.}[[:space:]]+${REGISTRY_NAME}([[:space:]]|\$)" "$hosts"; then
    warn "${REGISTRY_NAME} è già mappato a ${ip} in ${hosts}"
    return 0
  fi

  # se esiste una riga per 'registry' con un altro IP, la sostituiamo
  if grep -Eq "^[[:space:]]*[0-9.:a-fA-F]+[[:space:]]+${REGISTRY_NAME}([[:space:]]|\$)" "$hosts"; then
    log "Aggiorno mapping esistente per ${REGISTRY_NAME} in ${hosts}"
    sudo sed -i.bak -E "s|^[[:space:]]*[0-9.:a-fA-F]+[[:space:]]+(${REGISTRY_NAME})([[:space:]]|\$)|${ip}\t\1\2|" "$hosts"
  else
    log "Aggiungo mapping ${REGISTRY_NAME} -> ${ip} in ${hosts}"
    echo -e "${ip}\t${REGISTRY_NAME}" | sudo tee -a "$hosts" >/dev/null
  fi
}

wait_registry_ready() {
  # Consideriamo "ready" anche HTTP 401 perché il registry con auth risponde 401 quando è UP
  local host="$1" port="$2" timeout="${3:-60}"
  local i=0 code=000
  while [[ $i -lt $timeout ]]; do
    code="$(curl -sk -o /dev/null -w '%{http_code}' "https://${host}:${port}/v2/")" || true
    if [[ "$code" == "200" || "$code" == "401" ]]; then
      return 0
    fi
    sleep 1; i=$((i+1))
  done
  echo "Timeout: registry ${host}:${port} non pronto (ultimo HTTP ${code})" >&2
  return 1
}

check_and_label() {
  NODE=$1
  LABEL_KEY=$2
  LABEL_VALUE=$3

  # Controllo se il nodo ha già la label con quel valore
  CURRENT_VALUE=$(kubectl get node "$NODE" -o jsonpath="{.metadata.labels.$LABEL_KEY}")

  if [ "$CURRENT_VALUE" == "$LABEL_VALUE" ]; then
    warn "Nodo $NODE ha già label $LABEL_KEY=$LABEL_VALUE, salto."
  else
    log "Applico label $LABEL_KEY=$LABEL_VALUE a $NODE"
    kubectl label node "$NODE" "$LABEL_KEY=$LABEL_VALUE" --overwrite
  fi
}

create_kind_cluster() {
  log "Creazione cluster Kind \"$CLUSTER_NAME\" con $NUM_WORKERS worker(s)..."

  # Costruzione dinamica dei nodi worker
  WORKER_NODES=""
  for ((i = 0; i < NUM_WORKERS; i++)); do
  WORKER_NODES+="
  - role: worker
    kubeadmConfigPatches:
    - |
      kind: JoinConfiguration
      nodeRegistration:
        kubeletExtraArgs:
          read-only-port: \"10255\"
          address: \"0.0.0.0\"
"
  done

  cat <<EOF | kind create cluster --name "$CLUSTER_NAME" --config=-
kind: Cluster
apiVersion: kind.x-k8s.io/v1alpha4
networking:
  disableDefaultCNI: true
  podSubnet: "10.244.0.0/16"
  serviceSubnet: "10.96.0.0/12"
containerdConfigPatches:
  - |
    [plugins."io.containerd.grpc.v1.cri".registry]
      [plugins."io.containerd.grpc.v1.cri".registry.mirrors."${REGISTRY_NAME}:${REGISTRY_PORT}"]
        endpoint = ["https://${REGISTRY_NAME}:${REGISTRY_PORT}"]
      [plugins."io.containerd.grpc.v1.cri".registry.configs."${REGISTRY_NAME}:${REGISTRY_PORT}".auth]
        username = "${REGISTRY_USER}"
        password = "${REGISTRY_PASS}"
      [plugins."io.containerd.grpc.v1.cri".registry.configs."${REGISTRY_NAME}:${REGISTRY_PORT}".tls]
        insecure_skip_verify = true
      [plugins."io.containerd.grpc.v1.cri".registry.mirrors."${REGISTRY_MALICIOUS_NAME}:${REGISTRY_PORT}"]
        endpoint = ["https://${REGISTRY_NAME}:${REGISTRY_PORT}"]
      [plugins."io.containerd.grpc.v1.cri".registry.configs."${REGISTRY_MALICIOUS_NAME}:${REGISTRY_PORT}".auth]
        username = "${REGISTRY_USER}"
        password = "${REGISTRY_PASS}"
      [plugins."io.containerd.grpc.v1.cri".registry.configs."${REGISTRY_MALICIOUS_NAME}:${REGISTRY_PORT}".tls]
        insecure_skip_verify = true
      [plugins."io.containerd.grpc.v1.cri".containerd.runtimes.runc.options]
        SystemdCgroup = true
nodes:
  - role: control-plane
    kubeadmConfigPatches:
    - |
      apiVersion: kubeadm.k8s.io/v1beta3
      kind: ClusterConfiguration
      apiServer:
        extraArgs:
          anonymous-auth: "true"
    extraPortMappings:
      - containerPort: ${REGISTRY_PORT}
    extraMounts:
      - hostPath: $(pwd)/pb/certs
        containerPath: /certs
$WORKER_NODES
EOF
}

# ===== 1) Avvio kind, copio certificato e metto label su nodi =====
rm -f "./pb/docker/apiserver/apiserver.crt" "./pb/docker/apiserver/apiserver.key"
if kind get clusters | grep -q "^$CLUSTER_NAME$"; then
  warn "Cluster \"$CLUSTER_NAME\" già esistente, nessuna creazione necessaria."
else
  create_kind_cluster
fi
docker cp $CLUSTER_NAME-control-plane:/etc/kubernetes/pki/apiserver.crt ./pb/docker/apiserver/apiserver.crt
docker cp $CLUSTER_NAME-control-plane:/etc/kubernetes/pki/apiserver.key ./pb/docker/apiserver/apiserver.key
check_and_label "$CLUSTER_NAME-worker" group $LABEL_NODE_ATTACKER
check_and_label "$CLUSTER_NAME-worker2" group $LABEL_NOT_ATTACKER

# ===== 2) Generazione certificati e route per l'host =====
if [ ! -f "$HTPASSWD_PATH" ]; then
  log "Generazione file htpasswd..."
  mkdir -p "$(dirname "$HTPASSWD_PATH")"
  htpasswd -Bbn "$REGISTRY_USER" "$REGISTRY_PASS" > "$HTPASSWD_PATH"
else
  warn "File htpasswd già esistente."
fi

if [ ! -f "$CERT_CRT_PATH" ] || [ ! -f "$CERT_KEY_PATH" ]; then
  log "Generazione certificati TLS self-signed..."
  mkdir -p "$(dirname "$CERT_CRT_PATH")"
  mkdir -p "$(dirname "$CERT_KEY_PATH")"
  openssl req -newkey rsa:4096 -nodes -sha256 \
    -keyout "$CERT_KEY_PATH" \
    -x509 -days 365 \
    -out "$CERT_CRT_PATH" \
    -subj "/CN=$REGISTRY_CN"
else
  warn "Certificati TLS già esistenti."
fi

ensure_registry_hosts

# ===== 3) Avvio docker compose e aspetto registry sia funzionante per fare il login =====
log "Avvio servizi con docker compose..."
docker compose -f ./pb/docker/docker-compose.yml up -d --build
wait_registry_ready "${REGISTRY_NAME}" "${REGISTRY_PORT}" 300 || exit 1
docker login ${REGISTRY_NAME}:${REGISTRY_PORT} -u "$REGISTRY_USER" -p "$REGISTRY_PASS"