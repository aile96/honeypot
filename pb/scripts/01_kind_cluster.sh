#!/usr/bin/env bash
set -euo pipefail

SCRIPTS_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMMON_LIB="${SCRIPTS_ROOT}/lib/common.sh"
if [[ ! -f "${COMMON_LIB}" ]]; then
  printf "[ERROR] Common library not found: %s\n" "${COMMON_LIB}" >&2
  return 1 2>/dev/null || exit 1
fi
source "${COMMON_LIB}"

# --- Config ---
# If KIND_CLUSTER=0 -> target is minikube; otherwise target is kind
KIND_CLUSTER="${KIND_CLUSTER:-0}"
TARGET="${TARGET:-$([ "$KIND_CLUSTER" = "0" ] && echo "minikube" || echo "kind")}"
CLUSTER_PROFILE="${CLUSTER_PROFILE:-honeypotlab}"
KIND_CLUSTER_NAME="${KIND_CLUSTER_NAME:-${CLUSTER_PROFILE}}"
MINIKUBE_PROFILE="${MINIKUBE_PROFILE:-${CLUSTER_PROFILE}}"
[[ -n "${KIND_CLUSTER_NAME}" ]] || die "KIND_CLUSTER_NAME must not be empty."
[[ -n "${MINIKUBE_PROFILE}" ]] || die "MINIKUBE_PROFILE must not be empty."

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

# System requirements
REQUIRED_AVAIL_MEM_MB="${REQUIRED_AVAIL_MEM_MB:-12288}" # 12 GB
REQUIRED_CPUS="${REQUIRED_CPUS:-4}"
SKIP_RESOURCE_CHECK="${SKIP_RESOURCE_CHECK:-false}"
normalize_bool_var SKIP_RESOURCE_CHECK
LOAD_IMAGES="${LOAD_IMAGES:-false}"
normalize_bool_var LOAD_IMAGES
IMAGE_PARALLELISM="${IMAGE_PARALLELISM:-8}"
[[ "${IMAGE_PARALLELISM}" =~ ^[0-9]+$ ]] || die "IMAGE_PARALLELISM must be an integer >= 1"
(( IMAGE_PARALLELISM >= 1 )) || die "IMAGE_PARALLELISM must be >= 1"

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

# --- Profile-aware cluster detection ---
kind_profile_exists() {
  kind get clusters 2>/dev/null | grep -Fxq "${KIND_CLUSTER_NAME}"
}

kind_profile_reachable() {
  kubectl --context "kind-${KIND_CLUSTER_NAME}" get nodes --no-headers >/dev/null 2>&1
}

minikube_profile_exists() {
  local profiles_json
  profiles_json="$(minikube profile list -o json 2>/dev/null || true)"
  [[ -n "${profiles_json}" ]] || return 1

  if command -v jq >/dev/null 2>&1; then
    jq -e --arg profile "${MINIKUBE_PROFILE}" \
      '[.valid[]?.Name, .invalid[]?.Name] | index($profile) != null' \
      >/dev/null <<<"${profiles_json}"
  else
    printf '%s' "${profiles_json}" \
      | tr -d '[:space:]' \
      | grep -Fq "\"Name\":\"${MINIKUBE_PROFILE}\""
  fi
}

minikube_profile_reachable() {
  kubectl --context "${MINIKUBE_PROFILE}" get nodes --no-headers >/dev/null 2>&1
}

# --- System resource checks ---
get_mem_available_mb() {
  if [[ -r /proc/meminfo ]]; then
    awk '/MemAvailable:/ {print int($2/1024)}' /proc/meminfo
    return 0
  fi

  if command -v vm_stat >/dev/null 2>&1; then
    local pagesize free inactive speculative
    pagesize="$(vm_stat | awk '/page size of/ {gsub(/[^0-9]/, "", $8); print $8}')"
    free="$(vm_stat | awk '/Pages free/ {gsub(/[^0-9]/, "", $3); print $3}')"
    inactive="$(vm_stat | awk '/Pages inactive/ {gsub(/[^0-9]/, "", $3); print $3}')"
    speculative="$(vm_stat | awk '/Pages speculative/ {gsub(/[^0-9]/, "", $3); print $3}')"
    speculative="${speculative:-0}"

    if [[ -n "$pagesize" && -n "$free" && -n "$inactive" ]]; then
      echo $(( (free + inactive + speculative) * pagesize / 1024 / 1024 ))
      return 0
    fi
  fi

  return 1
}

get_cpu_count() {
  if command -v nproc >/dev/null 2>&1; then
    nproc
    return 0
  fi
  if command -v sysctl >/dev/null 2>&1; then
    sysctl -n hw.ncpu
    return 0
  fi
  return 1
}

check_system_resources() {
  local avail_mem_mb cpu_count

  avail_mem_mb="$(get_mem_available_mb || true)"
  cpu_count="$(get_cpu_count || true)"

  if [[ -z "${avail_mem_mb}" || "${avail_mem_mb}" -le 0 ]]; then
    die "Unable to determine available RAM. Need at least 12 GB available."
  fi
  if (( avail_mem_mb < REQUIRED_AVAIL_MEM_MB )); then
    die "Not enough available RAM: ${avail_mem_mb} MB available, need at least ${REQUIRED_AVAIL_MEM_MB} MB."
  fi

  if [[ -z "${cpu_count}" || "${cpu_count}" -le 0 ]]; then
    die "Unable to determine CPU count. Need at least 4 CPUs."
  fi
  if (( cpu_count < REQUIRED_CPUS )); then
    die "Not enough CPU cores: ${cpu_count} available, need at least ${REQUIRED_CPUS}."
  fi
}

# --- Create Kind cluster ---
create_kind_cluster() {
  log "Creating Kind cluster '${KIND_CLUSTER_NAME}' with ${KIND_WORKERS} worker(s)..."

  local WORKER_NODES=""
  for ((i=0; i< KIND_WORKERS; i++)); do
    WORKER_NODES+="
  - role: worker
"
  done

  cat <<EOF | kind create cluster --name "${KIND_CLUSTER_NAME}" --image "kindest/node:v${K8S_VERSION}" --config=-
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
  log "Creating Minikube profile '${MINIKUBE_PROFILE}' with ${MINIKUBE_NODES} node(s) (${MINIKUBE_CPUS} CPUs, ${MINIKUBE_MEM_MB} MB)..."
  minikube start \
    --profile="${MINIKUBE_PROFILE}" \
    --kubernetes-version="v${K8S_VERSION}" \
    --listen-address=0.0.0.0 \
    --nodes="${MINIKUBE_NODES}" \
    --cpus="${MINIKUBE_CPUS}" \
    --memory="${MINIKUBE_MEM_MB}"
}

run_images_in_parallel() {
  local worker="$1"
  shift

  local -a pids=()
  local -a labels=()
  local -a failed=()
  local img

  for img in "$@"; do
    "${worker}" "${img}" &
    pids+=("$!")
    labels+=("${img}")

    if (( ${#pids[@]} >= IMAGE_PARALLELISM )); then
      local pid="${pids[0]}"
      local label="${labels[0]}"
      pids=("${pids[@]:1}")
      labels=("${labels[@]:1}")
      if ! wait "${pid}"; then
        failed+=("${label}")
      fi
    fi
  done

  local i
  for i in "${!pids[@]}"; do
    if ! wait "${pids[$i]}"; then
      failed+=("${labels[$i]}")
    fi
  done

  if (( ${#failed[@]} > 0 )); then
    die "Image operation failed for: ${failed[*]}"
  fi
}

pull_image_if_needed() {
  local img="$1"
  if docker image inspect "${img}" >/dev/null 2>&1; then
    warn "${img} already present locally, skipping."
    return 0
  fi
  log "${img} not found locally, pulling..."
  docker pull "${img}" >/dev/null
}

load_image_into_kind() {
  local img="$1"
  kind load docker-image --name "${KIND_CLUSTER_NAME}" "${img}" >/dev/null
}

load_image_into_minikube() {
  local img="$1"
  minikube -p "${MINIKUBE_PROFILE}" image load "${img}" >/dev/null
}

# --- Pull images if missing ---
pull_images_if_needed() {
  log "Checking local images and pulling if needed (parallelism=${IMAGE_PARALLELISM})..."
  run_images_in_parallel pull_image_if_needed "${IMAGES[@]}"
}

# --- Load images into a freshly created cluster ---
load_images_into_kind() {
  log "Loading images into Kind cluster '${KIND_CLUSTER_NAME}' (parallelism=${IMAGE_PARALLELISM})..."
  run_images_in_parallel load_image_into_kind "${IMAGES[@]}"
}

load_images_into_minikube() {
  log "Loading images into Minikube profile '${MINIKUBE_PROFILE}' (parallelism=${IMAGE_PARALLELISM})..."
  run_images_in_parallel load_image_into_minikube "${IMAGES[@]}"
}

# =======================
# === Decision flow    ===
# =======================

case "${TARGET}" in
  kind)
    TARGET_CONTEXT="kind-${KIND_CLUSTER_NAME}"
    ;;
  minikube)
    TARGET_CONTEXT="${MINIKUBE_PROFILE}"
    ;;
  *)
    die "Invalid TARGET: $TARGET (allowed: kind|minikube)"
    ;;
esac

if [[ -z "${KUBE_CONTEXT:-}" ]]; then
  KUBE_CONTEXT="${TARGET_CONTEXT}"
fi
export KUBE_CONTEXT
log "Requested target: ${TARGET} (context: ${KUBE_CONTEXT})"

case "$TARGET" in
  kind)
    if kind_profile_exists; then
      if kind_profile_reachable; then
        warn "Kind cluster '${KIND_CLUSTER_NAME}' is already reachable."
        warn "Full skip: no creation, no image pulls, no image loads."
        return 0 2>/dev/null || exit 0
      fi
      warn "Kind cluster '${KIND_CLUSTER_NAME}' exists but context 'kind-${KIND_CLUSTER_NAME}' is not reachable. Re-exporting kubeconfig..."
      kind export kubeconfig --name "${KIND_CLUSTER_NAME}" >/dev/null 2>&1 || true
      if kind_profile_reachable; then
        warn "Kind cluster '${KIND_CLUSTER_NAME}' became reachable after kubeconfig export."
        warn "Full skip: no creation, no image pulls, no image loads."
        return 0 2>/dev/null || exit 0
      fi
      die "Kind cluster '${KIND_CLUSTER_NAME}' exists but is still not reachable via context 'kind-${KIND_CLUSTER_NAME}'."
    fi

    log "No Kind cluster named '${KIND_CLUSTER_NAME}' detected. Creating..."
    if is_true "${SKIP_RESOURCE_CHECK}"; then
      warn "Skipping host resource checks because SKIP_RESOURCE_CHECK=true."
    else
      check_system_resources
    fi
    create_kind_cluster
    if is_true "${LOAD_IMAGES}"; then
      pull_images_if_needed
      load_images_into_kind
    else
      warn "Skipping image pre-pull/load because LOAD_IMAGES=false."
    fi
    ;;
  minikube)
    if minikube_profile_exists; then
      if minikube_profile_reachable; then
        warn "Minikube profile '${MINIKUBE_PROFILE}' is already reachable."
        warn "Full skip: no creation, no image pulls, no image loads."
        return 0 2>/dev/null || exit 0
      fi

      log "Minikube profile '${MINIKUBE_PROFILE}' exists but is not reachable. Starting it..."
      if is_true "${SKIP_RESOURCE_CHECK}"; then
        warn "Skipping host resource checks because SKIP_RESOURCE_CHECK=true."
      else
        check_system_resources
      fi
      create_minikube_cluster
      log "Minikube profile '${MINIKUBE_PROFILE}' started."
      return 0 2>/dev/null || exit 0
    fi

    log "No Minikube profile named '${MINIKUBE_PROFILE}' detected. Creating..."
    if is_true "${SKIP_RESOURCE_CHECK}"; then
      warn "Skipping host resource checks because SKIP_RESOURCE_CHECK=true."
    else
      check_system_resources
    fi
    create_minikube_cluster
    if is_true "${LOAD_IMAGES}"; then
      pull_images_if_needed
      load_images_into_minikube
    else
      warn "Skipping image pre-pull/load because LOAD_IMAGES=false."
    fi
    ;;
esac

log "Cluster ready (target context: ${KUBE_CONTEXT})."
