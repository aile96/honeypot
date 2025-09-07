#!/bin/bash

CLUSTER_NAME=$1
NUM_WORKERS=$2
REGISTRY_NAME=$3
REGISTRY_PORT=$4
REGISTRY_USER=$5
REGISTRY_PASS=$6

create_kind_cluster() {
  log "Creazione cluster Kind \"$CLUSTER_NAME\" con $NUM_WORKERS worker(s)..."

  # Costruzione dinamica dei nodi worker
  WORKER_NODES=""
  for ((i = 0; i < NUM_WORKERS; i++)); do
    WORKER_NODES+="  - role: worker"$'\n'
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
nodes:
  - role: control-plane
    extraPortMappings:
      - containerPort: ${REGISTRY_PORT}
    extraMounts:
      - hostPath: $(pwd)/pb/certs
        containerPath: /certs
$WORKER_NODES
EOF
}


if kind get clusters | grep -q "^$CLUSTER_NAME$"; then
  warn "Cluster \"$CLUSTER_NAME\" già esistente, nessuna creazione necessaria."
else
  create_kind_cluster
fi