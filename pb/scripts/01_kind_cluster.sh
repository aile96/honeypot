#!/usr/bin/env bash
set -euo pipefail

### === Utility functions ===
log()  { printf "\033[1;36m[INFO]\033[0m %s\n" "$*"; }
warn() { printf "\033[1;33m[WARN]\033[0m %s\n" "$*"; }
err()  { printf "\033[1;31m[ERR ]\033[0m %s\n" "$*" >&2; }
die()  { echo -e "[ERROR] $*" >&2; exit 1; }

# --- Config ---
# If KIND_CLUSTER=0 -> target is minikube; otherwise target is kind
KIND_CLUSTER="${KIND_CLUSTER:-0}"
TARGET="${TARGET:-$([ "$KIND_CLUSTER" = "0" ] && echo "minikube" || echo "kind")}"

K8S_VERSION="${K8S_VERSION:-1.30.0}"

# Unified workers:
# - Kind: exactly WORKERS workers (+ control-plane)
# - Minikube: (WORKERS + 1) total nodes
WORKERS="${WORKERS:-2}"
[[ "$WORKERS" =~ ^[0-9]+$ ]] || die "WORKERS must be a non-negative integer"

KIND_WORKERS="$WORKERS"
MINIKUBE_NODES="$((WORKERS + 1))"

# Minikube resources
MINIKUBE_CPUS="${MINIKUBE_CPUS:-4}"
MINIKUBE_MEM_MB="${MINIKUBE_MEM_MB:-4896}"

# Images to pre-pull & load ONLY when creating a brand-new cluster
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
  registry.k8s.io/sig-storage/smbplugin:v1.19.1
)

# --- Generic cluster detection (no kind/minikube commands) ---
cluster_reachable() {
  # If kubectl can list nodes, a cluster exists (regardless of provider)
  kubectl get nodes --no-headers >/dev/null 2>&1
}

detect_current_provider() {
  # Best-effort, for logging only
  local ctx
  ctx="$(kubectl config current-context 2>/dev/null || true)"
  if [[ "$ctx" == kind-* ]]; then
    echo "kind ($ctx)"
  elif [[ "$ctx" == minikube* || "$ctx" == *minikube* ]]; then
    echo "minikube ($ctx)"
  else
    # fallback by node names
    if kubectl get nodes -o name 2>/dev/null | grep -q "kind-control-plane"; then
      echo "kind (by node name)"
    elif kubectl get nodes -o name 2>/dev/null | grep -qi "minikube"; then
      echo "minikube (by node name)"
    else
      echo "unknown ($ctx)"
    fi
  fi
}

# --- Create Kind cluster ---
create_kind_cluster() {
  log "Creating Kind cluster with ${KIND_WORKERS} worker(s)..."

  local WORKER_NODES=""
  for ((i=0; i< KIND_WORKERS; i++)); do
    WORKER_NODES+="
  - role: worker
"
  done

  cat <<EOF | kind create cluster --image "kindest/node:v${K8S_VERSION}" --config=-
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

# --- Create Minikube cluster ---
create_minikube_cluster() {
  log "Creating Minikube cluster with ${MINIKUBE_NODES} node(s) (${MINIKUBE_CPUS} CPUs, ${MINIKUBE_MEM_MB} MB)..."
  minikube start --driver=docker \
    --kubernetes-version="v${K8S_VERSION}" \
    --listen-address=0.0.0.0 \
    --container-runtime=containerd \
    --nodes="${MINIKUBE_NODES}" \
    --cpus="${MINIKUBE_CPUS}" \
    --memory="${MINIKUBE_MEM_MB}"
}

# --- Pull images if missing ---
pull_images_if_needed() {
  log "Checking local images and pulling if needed..."
  for img in "${IMAGES[@]}"; do
    if docker image inspect "$img" >/dev/null 2>&1; then
      warn "$img already present locally, skipping."
    else
      log "$img not found locally — pulling..."
      docker pull "$img"
    fi
  done
}

# --- Load images into a freshly created cluster ---
load_images_into_kind() {
  log "Loading images into Kind..."
  for img in "${IMAGES[@]}"; do
    kind load docker-image "$img"
  done
}

load_images_into_minikube() {
  log "Loading images into Minikube..."
  for img in "${IMAGES[@]}"; do
    minikube image load "$img"
  done
}

# =======================
# === Decision flow    ===
# =======================

log "Requested target: ${TARGET}"

if cluster_reachable; then
  prov="$(detect_current_provider)"
  warn "A Kubernetes cluster is already reachable via kubectl (provider: ${prov})."
  warn "Full skip: no creation, no image pulls, no image loads."
  return 0
fi

log "No reachable cluster detected. Creating '${TARGET}'..."
case "$TARGET" in
  kind)
    create_kind_cluster
    pull_images_if_needed
    load_images_into_kind
    ;;
  minikube)
    create_minikube_cluster
    pull_images_if_needed
    load_images_into_minikube
    ;;
  *)
    die "Invalid TARGET: $TARGET (allowed: kind|minikube)"
    ;;
esac

log "Cluster ready."
