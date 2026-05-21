#!/usr/bin/env bash
set -euo pipefail

# ==============================================================================
# Config
# ==============================================================================

INTERVAL="${HP_DOS_INTERVAL:-3}"
DESTRUCTIVE_LABEL_KEY="${HP_DESTRUCTIVE_LABEL_KEY:-honeypot.lab/destructive-ok}"
DESTRUCTIVE_LABEL_VALUE="${HP_DESTRUCTIVE_LABEL_VALUE:-true}"
PAUSE_KUBELET="${HP_DOS_PAUSE_KUBELET:-false}"

REMOTE_CMD_RAW="${1:-}"
if [[ -z "$REMOTE_CMD_RAW" ]]; then
  echo "Usage: $0 '<remote command prefix>'" >&2
  exit 1
fi

read -r -a REMOTE_CMD <<< "$REMOTE_CMD_RAW"

CRICTL_BASE=("${REMOTE_CMD[@]}" crictl)
CRICTL=()
KUBELET_PIDS=()

PROTECTED_CONTAINERS=(
  "agent"
  "opensearch"
  "grafana"
  "otel-collector"
  "jaeger"
  "prometheus"
)

RUNTIME_ENDPOINT_CANDIDATES=(
  "unix:///host/run/cri-dockerd.sock"
  "unix:///host/var/run/cri-dockerd.sock"
  "unix:///host/run/containerd/containerd.sock"
  "unix:///host/var/run/containerd/containerd.sock"
  "unix:///run/cri-dockerd.sock"
  "unix:///run/containerd/containerd.sock"
)

# ==============================================================================
# Cleanup
# ==============================================================================

cleanup() {
  if (( ${#KUBELET_PIDS[@]} > 0 )); then
    "${REMOTE_CMD[@]}" kill -CONT "${KUBELET_PIDS[@]}" >/dev/null 2>&1 || true
  fi
}

trap cleanup EXIT INT TERM

# ==============================================================================
# Runtime detection
# ==============================================================================

detect_runtime_endpoint() {
  local endpoint

  for endpoint in "${RUNTIME_ENDPOINT_CANDIDATES[@]}"; do
    if "${CRICTL_BASE[@]}" --runtime-endpoint "$endpoint" info >/dev/null 2>&1; then
      printf "%s\n" "$endpoint"
      return 0
    fi
  done

  return 1
}

init_crictl() {
  local runtime_endpoint

  runtime_endpoint="$(detect_runtime_endpoint)" || {
    echo "No working CRI endpoint found"
    exit 1
  }

  CRICTL=("${CRICTL_BASE[@]}" --runtime-endpoint "$runtime_endpoint")

  echo "Using CRI endpoint: $runtime_endpoint"
}

# ==============================================================================
# Kubelet handling
# ==============================================================================

find_kubelet_pids() {
  mapfile -t KUBELET_PIDS < <(
    "${REMOTE_CMD[@]}" pidof kubelet 2>/dev/null \
      | tr " " "\n" \
      | sed "/^$/d"
  )

  if (( ${#KUBELET_PIDS[@]} == 0 )); then
    echo "No kubelet PID found"
    exit 1
  fi
}

bool_true() {
  case "${1:-}" in
    1|true|TRUE|yes|YES|on|ON) return 0 ;;
    *) return 1 ;;
  esac
}

pause_kubelet() {
  if ! bool_true "$PAUSE_KUBELET"; then
    echo "Kubelet pause disabled: HP_DOS_PAUSE_KUBELET is not true"
    return 0
  fi

  echo "Pausing kubelet PID(s): ${KUBELET_PIDS[*]}"
  "${REMOTE_CMD[@]}" kill -STOP "${KUBELET_PIDS[@]}"
}

# ==============================================================================
# Container filtering
# ==============================================================================

is_protected_container() {
  local container_name="$1"

  printf "%s\n" "${PROTECTED_CONTAINERS[@]}" \
    | grep -Fxq "$container_name"
}

container_name_for_id() {
  local container_id="$1"

  "${CRICTL[@]}" inspect "$container_id" 2>/dev/null \
    | jq -r ".status.metadata.name // empty"
}

containers_for_pod() {
  local pod_id="$1"

  "${CRICTL[@]}" ps -q --pod "$pod_id"
}

pod_identity() {
  local pod_id="$1"

  "${CRICTL[@]}" inspectp "$pod_id" 2>/dev/null \
    | jq -r '
      (
        .status.metadata.namespace
        // .status.labels?["io.kubernetes.pod.namespace"]
        // .info.config.metadata.namespace
        // .info.config.labels?["io.kubernetes.pod.namespace"]
        // "unknown"
      )
      + "/"
      + (
        .status.metadata.name
        // .status.labels?["io.kubernetes.pod.name"]
        // .info.config.metadata.name
        // .info.config.labels?["io.kubernetes.pod.name"]
        // "unknown"
      )
    '
}

filtered_containers_for_pod() {
  local pod_id="$1"
  local container_id
  local container_name

  while IFS= read -r container_id; do
    [[ -z "$container_id" ]] && continue

    container_name="$(container_name_for_id "$container_id")"

    if is_protected_container "$container_name"; then
      echo "Container $container_id ($container_name) protected: skipping"
      continue
    fi

    printf "%s\n" "$container_id"
  done < <(containers_for_pod "$pod_id")
}

# ==============================================================================
# Pod handling
# ==============================================================================

list_target_pods() {
  "${CRICTL[@]}" pods -o json \
    | jq -r \
      --arg key "$DESTRUCTIVE_LABEL_KEY" \
      --arg value "$DESTRUCTIVE_LABEL_VALUE" '
        .items[]
        | select(.state == "SANDBOX_READY")
        | select(.labels[$key] == $value)
        | [.id, .metadata.namespace, .metadata.name]
        | @tsv
      '
}

log_skipped_pods() {
  "${CRICTL[@]}" pods -o json \
    | jq -r \
      --arg key "$DESTRUCTIVE_LABEL_KEY" \
      --arg value "$DESTRUCTIVE_LABEL_VALUE" '
        .items[]
        | select(.state == "SANDBOX_READY")
        | select(.labels[$key] != $value)
        | [
            (.metadata.namespace // "unknown"),
            (.metadata.name // "unknown")
          ]
        | @tsv
      ' \
    | while IFS="$(printf '\t')" read -r namespace pod_name; do
        [[ -z "${pod_name:-}" ]] && continue
        echo "Pod ${namespace}/${pod_name} skipped: missing ${DESTRUCTIVE_LABEL_KEY}=${DESTRUCTIVE_LABEL_VALUE}" >&2
      done
}

target_pods_exist() {
  local first_target

  first_target="$(
    list_target_pods | head -n 1 || true
  )"

  [[ -n "$first_target" ]]
}

stop_container() {
  local container_id="$1"

  if "${CRICTL[@]}" stop "$container_id"; then
    return 0
  fi

  echo "WARN: stop failed for container $container_id"
  return 1
}

stop_pod_containers() {
  local pod_id="$1"
  local namespace="$2"
  local pod_name="$3"
  local container_id
  local filtered_containers=()
  local stopped_count=0

  mapfile -t filtered_containers < <(filtered_containers_for_pod "$pod_id")

  if (( ${#filtered_containers[@]} == 0 )); then
    echo "Pod ${namespace}/${pod_name} ($pod_id): no stoppable containers found"
    return 0
  fi

  echo "Pod ${namespace}/${pod_name} ($pod_id): stopping ${#filtered_containers[@]} container(s)"

  for container_id in "${filtered_containers[@]}"; do
    if stop_container "$container_id"; then
      stopped_count=$((stopped_count + 1))
    fi
  done

  echo "Pod ${namespace}/${pod_name} ($pod_id): stopped ${stopped_count}/${#filtered_containers[@]} container(s)"

  if (( stopped_count == 0 )); then
    return 1
  fi

  return 0
}

stop_all_pod_containers_once() {
  local target_pods=()
  local line
  local pod_id
  local namespace
  local pod_name
  local processed_count=0
  local stopped_pods_count=0
  local failed_pods_count=0

  mapfile -t target_pods < <(list_target_pods)

  if (( ${#target_pods[@]} == 0 )); then
    echo "No pod with ${DESTRUCTIVE_LABEL_KEY}=${DESTRUCTIVE_LABEL_VALUE} found."
    log_skipped_pods
    return 1
  fi

  echo "Found ${#target_pods[@]} target pod(s) with ${DESTRUCTIVE_LABEL_KEY}=${DESTRUCTIVE_LABEL_VALUE}"

  for line in "${target_pods[@]}"; do
    IFS="$(printf '\t')" read -r pod_id namespace pod_name <<< "$line"

    [[ -z "${pod_id:-}" ]] && continue

    processed_count=$((processed_count + 1))

    if stop_pod_containers "$pod_id" "$namespace" "$pod_name"; then
      stopped_pods_count=$((stopped_pods_count + 1))
    else
      failed_pods_count=$((failed_pods_count + 1))
    fi
  done

  echo "Target pods processed: ${processed_count}; successful pod stop attempts: ${stopped_pods_count}; failed pod stop attempts: ${failed_pods_count}"

  if (( stopped_pods_count == 0 )); then
    echo "No containers were stopped in any target pod."
    return 1
  fi

  return 0
}

# ==============================================================================
# Main
# ==============================================================================

find_kubelet_pids
init_crictl

if ! target_pods_exist; then
  echo "No pod with ${DESTRUCTIVE_LABEL_KEY}=${DESTRUCTIVE_LABEL_VALUE} found."
  log_skipped_pods
  exit 1
fi

pause_kubelet

while :; do
  if ! stop_all_pod_containers_once; then
    echo "DOS loop iteration failed: no effective container stop performed."
    exit 1
  fi

  sleep "$INTERVAL"
done