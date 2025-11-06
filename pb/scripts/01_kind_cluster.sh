#!/bin/bash

### === Utility functions ===
log() { printf "\033[1;36m[INFO]\033[0m %s\n" "$*"; }
warn() { printf "\033[1;33m[WARN]\033[0m %s\n" "$*"; }
err() { printf "\033[1;31m[ERR ]\033[0m %s\n" "$*" >&2; }
die()   { echo -e "[ERROR] $*" >&2; exit 1; }

K8S_VERSION="1.30.0"
NUM_WORKERS="2"

create_kind_cluster() {
  log "Creation Kind cluster with $NUM_WORKERS worker(s)..."

  # Dynamic construction of workers
  WORKER_NODES=""
  for ((i = 0; i < NUM_WORKERS; i++)); do
  WORKER_NODES+="
  - role: worker
"
  done

  cat <<EOF | kind create cluster --image kindest/node:v${K8S_VERSION} --config=-
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

# Images to pre-pull and load into Minikube
IMAGES=(
  quay.io/metallb/controller:v0.15.2
  otel/opentelemetry-collector-contrib:0.120.0
  grafana/grafana:11.5.2
  opensearchproject/opensearch:2.19.0
  gcr.io/k8s-minikube/kicbase:v0.0.46
  quay.io/coreos/etcd:v3.5.10
  quay.io/frrouting/frr:9.1.0
  busybox:1.36
  quay.io/metallb/speaker:v0.15.2
  postgres:15-alpine
  registry.k8s.io/sig-storage/csi-provisioner:v5.3.0
  registry.k8s.io/sig-storage/livenessprobe:v2.17.0
  registry.k8s.io/sig-storage/csi-node-driver-registrar:v2.15.0
  quay.io/prometheus/prometheus:v3.1.0
)

log "Checking and pulling only if needed..."
for img in "${IMAGES[@]}"; do
  if docker image inspect "$img" >/dev/null 2>&1; then
    warn "$img already exists locally, skipping."
  else
    log "$img not found locally — pulling..."
    docker pull "$img"
  fi
done

log "Loading images into kind..."
for img in "${IMAGES[@]}"; do
  kind load docker-image "$img"
done

log "Done."