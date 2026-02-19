#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# === Load shared utilities ===
COMMON_LIB="${PROJECT_ROOT}/pb/scripts/lib/common.sh"
if [[ ! -f "$COMMON_LIB" ]]; then
  printf "[ERROR] Common library not found: %s\n" "$COMMON_LIB" >&2
  return 1 2>/dev/null || exit 1
fi
source "$COMMON_LIB"

# === Load variables from config file ===
CONFIG_LOADER="${PROJECT_ROOT}/pb/scripts/lib/load_config.sh"
if [[ ! -f "$CONFIG_LOADER" ]]; then
  err "Configuration loader not found: $CONFIG_LOADER"
  return 1 2>/dev/null || exit 1
fi
source "$CONFIG_LOADER"

ENV_FILE="${PROJECT_ROOT}/configuration.conf"
if ! load_env_file "$ENV_FILE"; then
  err "Failed to load configuration from: $ENV_FILE"
  return 1 2>/dev/null || exit 1
fi

CLUSTER_PROFILE="${CLUSTER_PROFILE:-honeypotlab}"
KIND_CLUSTER_NAME="${KIND_CLUSTER_NAME:-${CLUSTER_PROFILE}}"
MINIKUBE_PROFILE="${MINIKUBE_PROFILE:-${CLUSTER_PROFILE}}"
KIND_CONTEXT="kind-${KIND_CLUSTER_NAME}"
MINIKUBE_CONTEXT="${MINIKUBE_PROFILE}"
DOCKER_HELPER_NAME="${DOCKER_HELPER_NAME:-docker-cli-helper}"
DOCKER_HELPER_VOLUME="${DOCKER_HELPER_VOLUME:-${DOCKER_HELPER_NAME}-data}"

remove_containers() {
  if ! command -v docker >/dev/null 2>&1; then
    warn "docker not found, skipping container cleanup."
    return 0
  fi

  local -a requested=(
    "${CALDERA_SERVER:-}"
    "${ATTACKER:-}"
    "${CALDERA_CONTROLLER:-}"
    "${PROXY:-}"
    "${REGISTRY_NAME:-}"
    "${DOCKER_HELPER_NAME:-}"
    "load-generator"
    "samba-pv"
  )
  local -a existing=()
  local all_container_names=""
  local name

  if ! all_container_names="$(docker ps -a --format '{{.Names}}' 2>/dev/null)"; then
    warn "Docker daemon is not reachable, skipping container cleanup."
    return 0
  fi

  for name in "${requested[@]}"; do
    [[ -z "$name" ]] && continue
    if printf '%s\n' "$all_container_names" | grep -Fxq "$name"; then
      existing+=("$name")
    fi
  done

  if (( ${#existing[@]} == 0 )); then
    log "No matching containers to delete."
    return 0
  fi

  log "Deleting containers: ${existing[*]}"
  if docker rm -f "${existing[@]}" >/dev/null; then
    log "Container deletion completed."
  else
    warn "Container deletion ended with errors."
  fi
}

uninstall_helm_release() {
  local release="$1"
  local namespace="${2:-}"
  local kube_context="${3:-}"

  if ! command -v helm >/dev/null 2>&1; then
    warn "helm not found, skipping helm cleanup."
    return 0
  fi

  local -a helm_args=(uninstall "$release")
  if [[ -n "$namespace" ]]; then
    helm_args+=(--namespace "$namespace")
  fi
  if [[ -n "$kube_context" ]]; then
    helm_args+=(--kube-context "$kube_context")
  fi

  helm "${helm_args[@]}" >/dev/null 2>&1 || true
}

context_exists() {
  local kube_context="$1"
  kubectl config get-contexts -o name 2>/dev/null | grep -Fxq "$kube_context"
}

cleanup_helm_and_k8s() {
  if ! command -v kubectl >/dev/null 2>&1; then
    warn "kubectl not found, skipping Kubernetes object cleanup."
    return 0
  fi

  local -a contexts=()
  if context_exists "${KIND_CONTEXT}"; then
    contexts+=("${KIND_CONTEXT}")
  fi
  if context_exists "${MINIKUBE_CONTEXT}"; then
    contexts+=("${MINIKUBE_CONTEXT}")
  fi

  if (( ${#contexts[@]} == 0 )); then
    log "No Kubernetes contexts found for '${KIND_CONTEXT}' or '${MINIKUBE_CONTEXT}'. Skipping Helm/K8s cleanup."
    return 0
  fi

  local ctx
  for ctx in "${contexts[@]}"; do
    log "Removing Helm releases in context '${ctx}'..."
    uninstall_helm_release "csi-driver-smb" "kube-system" "$ctx"
    uninstall_helm_release "honeypot-additions" "" "$ctx"
    uninstall_helm_release "honeypot-telemetry" "${MEM_NAMESPACE:-mem}" "$ctx"
    uninstall_helm_release "honeypot-astronomy-shop" "" "$ctx"
    uninstall_helm_release "metallb" "metallb-system" "$ctx"

    kubectl --context "$ctx" delete -n kube-system daemonset.apps/node-agent >/dev/null 2>&1 || true
  done
}

minikube_profile_exists() {
  local profile="$1"
  local profiles_json
  profiles_json="$(minikube profile list -o json 2>/dev/null || true)"
  [[ -n "${profiles_json}" ]] || return 1

  if command -v jq >/dev/null 2>&1; then
    jq -e --arg profile "${profile}" \
      '[.valid[]?.Name, .invalid[]?.Name] | index($profile) != null' \
      >/dev/null <<<"${profiles_json}"
  else
    printf '%s' "${profiles_json}" \
      | tr -d '[:space:]' \
      | grep -Fq "\"Name\":\"${profile}\""
  fi
}

kind_cluster_exists() {
  local cluster="$1"
  kind get clusters 2>/dev/null | grep -Fxq "$cluster"
}

has_kind_clusters() {
  kind get clusters 2>/dev/null | grep -q .
}

has_minikube_profiles() {
  local profiles_json
  profiles_json="$(minikube profile list -o json 2>/dev/null || true)"
  [[ -n "${profiles_json}" ]] || return 1

  if command -v jq >/dev/null 2>&1; then
    jq -e '[.valid[]?.Name, .invalid[]?.Name] | length > 0' >/dev/null <<<"${profiles_json}"
  else
    printf '%s' "${profiles_json}" | grep -q '"Name":"'
  fi
}

cleanup_docker_helper_artifacts() {
  if ! command -v docker >/dev/null 2>&1; then
    return 0
  fi

  if [[ -n "${DOCKER_HELPER_NAME}" ]] && docker ps -a --format '{{.Names}}' | grep -Fxq "${DOCKER_HELPER_NAME}"; then
    log "Removing docker helper container: ${DOCKER_HELPER_NAME}"
    docker rm -f "${DOCKER_HELPER_NAME}" >/dev/null 2>&1 || warn "Failed to remove helper container '${DOCKER_HELPER_NAME}'."
  fi

  if [[ -n "${DOCKER_HELPER_VOLUME}" ]] && docker volume inspect "${DOCKER_HELPER_VOLUME}" >/dev/null 2>&1; then
    log "Removing docker helper volume: ${DOCKER_HELPER_VOLUME}"
    docker volume rm "${DOCKER_HELPER_VOLUME}" >/dev/null 2>&1 || warn "Failed to remove helper volume '${DOCKER_HELPER_VOLUME}'."
  fi
}

cleanup_shared_networks_if_unused() {
  if ! command -v docker >/dev/null 2>&1; then
    return 0
  fi

  if command -v kind >/dev/null 2>&1; then
    if has_kind_clusters; then
      log "Skipping Docker network cleanup for kind: other kind clusters still exist."
    else
      docker network rm kind >/dev/null 2>&1 || true
    fi
  fi

  if command -v minikube >/dev/null 2>&1; then
    if has_minikube_profiles; then
      log "Skipping Docker network cleanup for minikube: other minikube profiles still exist."
    else
      docker network rm minikube >/dev/null 2>&1 || true
    fi
  fi
}

delete_kind_cluster() {
  if command -v kind >/dev/null 2>&1; then
    if kind_cluster_exists "${KIND_CLUSTER_NAME}"; then
      log "Deleting kind cluster: ${KIND_CLUSTER_NAME}"
      kind delete cluster --name "${KIND_CLUSTER_NAME}" >/dev/null 2>&1 || true
    else
      log "Kind cluster '${KIND_CLUSTER_NAME}' not found."
    fi
  else
    warn "kind not found, skipping kind cluster deletion."
  fi
}

delete_minikube_cluster() {
  if command -v minikube >/dev/null 2>&1; then
    if minikube_profile_exists "${MINIKUBE_PROFILE}"; then
      log "Deleting minikube profile: ${MINIKUBE_PROFILE}"
      minikube delete -p "${MINIKUBE_PROFILE}" >/dev/null 2>&1 || true
    else
      log "Minikube profile '${MINIKUBE_PROFILE}' not found."
    fi
  else
    warn "minikube not found, skipping minikube cluster deletion."
  fi
}

remove_containers
cleanup_docker_helper_artifacts
cleanup_helm_and_k8s
delete_kind_cluster
delete_minikube_cluster
cleanup_shared_networks_if_unused
log "Cleanup completed."
