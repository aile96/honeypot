#!/bin/bash

### === Utility functions ===
log() { printf "\033[1;36m[INFO]\033[0m %s\n" "$*"; }
warn() { printf "\033[1;33m[WARN]\033[0m %s\n" "$*"; }
err() { printf "\033[1;31m[ERR ]\033[0m %s\n" "$*" >&2; }
die()   { echo -e "[ERROR] $*" >&2; exit 1; }

NUM_WORKERS=2

create_kind_cluster() {
  log "Creation Kind cluster with $NUM_WORKERS worker(s)..."

  # Dynamic construction of workers
  WORKER_NODES=""
  for ((i = 0; i < NUM_WORKERS; i++)); do
  WORKER_NODES+="
  - role: worker
"
  done

  cat <<EOF | kind create cluster --image kindest/node:v1.30.0 --config=-
kind: Cluster
apiVersion: kind.x-k8s.io/v1alpha4
networking:
  ipFamily: ipv4
  disableDefaultCNI: false
  podSubnet: "10.244.0.0/16"
  serviceSubnet: "10.96.0.0/12"
nodes:
  - role: control-plane
$WORKER_NODES
EOF
}

# ===== 1) Run kind, copy certificates and put label on nodes =====
if kind get clusters | grep -q "^kind$"; then
  warn "Cluster already existing, no action needed"
else
  create_kind_cluster
fi