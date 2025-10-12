#!/bin/bash

### === Utility functions ===
log() { printf "\033[1;36m[INFO]\033[0m %s\n" "$*"; }
warn() { printf "\033[1;33m[WARN]\033[0m %s\n" "$*"; }
err() { printf "\033[1;31m[ERR ]\033[0m %s\n" "$*" >&2; }
die()   { echo -e "[ERROR] $*" >&2; exit 1; }

ensure_registry_hosts() {
  local ip="127.0.0.1"
  local hosts="/etc/hosts"

  if grep -Eq "^[[:space:]]*${ip//./\\.}[[:space:]]+${REGISTRY_NAME}([[:space:]]|\$)" "$hosts"; then
    warn "${REGISTRY_NAME} is already mapped: ${ip} in ${hosts}"
    return 0
  fi

  # if exists one line for 'registry' with another IP, substituting
  if grep -Eq "^[[:space:]]*[0-9.:a-fA-F]+[[:space:]]+${REGISTRY_NAME}([[:space:]]|\$)" "$hosts"; then
    log "Updating existing mapping for ${REGISTRY_NAME} in ${hosts}"
    sudo sed -i.bak -E "s|^[[:space:]]*[0-9.:a-fA-F]+[[:space:]]+(${REGISTRY_NAME})([[:space:]]|\$)|${ip}\t\1\2|" "$hosts"
  else
    log "Adding mapping ${REGISTRY_NAME} -> ${ip} in ${hosts}"
    echo -e "${ip}\t${REGISTRY_NAME}" | sudo tee -a "$hosts" >/dev/null
  fi
}

wait_registry_ready() {
  # Also 401 is "Ready"
  local host="$1" port="$2" timeout="${3:-60}"
  local i=0 code=000
  while [[ $i -lt $timeout ]]; do
    code="$(curl -sk -o /dev/null -w '%{http_code}' "https://${host}:${port}/v2/")" || true
    if [[ "$code" == "200" || "$code" == "401" ]]; then
      return 0
    fi
    sleep 1; i=$((i+1))
  done
  echo "Timeout: registry ${host}:${port} not ready (last HTTP: ${code})" >&2
  return 1
}

check_and_label() {
  NODE=$1
  LABEL_KEY=$2
  LABEL_VALUE=$3

  # Check if this label already exists
  CURRENT_VALUE=$(kubectl get node "$NODE" -o jsonpath="{.metadata.labels.$LABEL_KEY}")

  if [ "$CURRENT_VALUE" == "$LABEL_VALUE" ]; then
    warn "Node $NODE already labeled $LABEL_KEY=$LABEL_VALUE, skip."
  else
    log "Applying label $LABEL_KEY=$LABEL_VALUE to $NODE"
    kubectl label node "$NODE" "$LABEL_KEY=$LABEL_VALUE" --overwrite
  fi
}

create_kind_cluster() {
  log "Creation Kind cluster \"$CLUSTER_NAME\" with $NUM_WORKERS worker(s)..."

  # Dynamic construction of workers
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
  ipFamily: ipv4
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
      etcd:
        local:
          extraArgs:
            listen-client-urls: "https://127.0.0.1:2379,http://0.0.0.0:12379"
$WORKER_NODES
EOF
}

# ===== 1) Run kind, copy certificates and put label on nodes =====
rm -f ./pb/docker/apiserver/*
if kind get clusters | grep -q "^$CLUSTER_NAME$"; then
  warn "Cluster \"$CLUSTER_NAME\" already existing, no action needed"
else
  create_kind_cluster
fi
docker cp $CLUSTER_NAME-control-plane:/etc/kubernetes/pki/apiserver.crt ./pb/docker/apiserver/apiserver.crt
docker cp $CLUSTER_NAME-control-plane:/etc/kubernetes/pki/apiserver.key ./pb/docker/apiserver/apiserver.key
check_and_label "$CLUSTER_NAME-worker" group $LABEL_NODE_ATTACKER
check_and_label "$CLUSTER_NAME-worker2" group $LABEL_NOT_ATTACKER

# ===== 2) Generation of certificates and route for host =====
if [ ! -f "$HTPASSWD_PATH" ]; then
  log "Generating htpasswd file..."
  mkdir -p "$(dirname "$HTPASSWD_PATH")"
  htpasswd -Bbn "$REGISTRY_USER" "$REGISTRY_PASS" > "$HTPASSWD_PATH"
else
  warn "File htpasswd already existing, no action needed"
fi

if [ ! -f "$CERT_CRT_PATH" ] || [ ! -f "$CERT_KEY_PATH" ]; then
  log "Generation of self-signed TLS certificates..."
  mkdir -p "$(dirname "$CERT_CRT_PATH")"
  mkdir -p "$(dirname "$CERT_KEY_PATH")"
  openssl req -newkey rsa:4096 -nodes -sha256 \
    -keyout "$CERT_KEY_PATH" \
    -x509 -days 365 \
    -out "$CERT_CRT_PATH" \
    -subj "/CN=$REGISTRY_CN"
else
  warn "TLS certificates already existing, no action needed"
fi

ensure_registry_hosts

# ===== 3) Running docker compose and waiting for registry to login =====
log "Running docker compose..."
docker compose -f ./pb/docker/docker-compose.yml up -d --build
wait_registry_ready "${REGISTRY_NAME}" "${REGISTRY_PORT}" 300 || exit 1
docker login ${REGISTRY_NAME}:${REGISTRY_PORT} -u "$REGISTRY_USER" -p "$REGISTRY_PASS"