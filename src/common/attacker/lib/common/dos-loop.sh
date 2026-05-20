#!/usr/bin/env bash
set -euo pipefail

# ==============================================================================
# Config
# ==============================================================================

INTERVAL="3"
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

pod_label_value() {
  local pod_id="$1"

  "${CRICTL[@]}" inspectp "$pod_id" 2>/dev/null \
    | jq -r --arg key "$DESTRUCTIVE_LABEL_KEY" '
      .status.labels?[$key]
      // .info.config.labels?[$key]
      // .info.runtimeSpec.annotations?[$key]
      // empty
    '
}

pod_has_destructive_label() {
  local pod_id="$1"
  local value

  value="$(pod_label_value "$pod_id")"
  [[ "$value" == "$DESTRUCTIVE_LABEL_VALUE" ]]
}

pod_identity() {
  local pod_id="$1"

  "${CRICTL[@]}" inspectp "$pod_id" 2>/dev/null \
    | jq -r '
      (
        .status.metadata.namespace
        // .status.labels?["io.kubernetes.pod.namespace"]
        // "unknown"
      )
      + "/"
      + (
        .status.metadata.name
        // .status.labels?["io.kubernetes.pod.name"]
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
      continue
    fi

    printf "%s\n" "$container_id"
  done < <(containers_for_pod "$pod_id")
}

# ==============================================================================
# Pod handling
# ==============================================================================

list_pods() {
  "${CRICTL[@]}" pods -q
}

list_target_pods() {
  local pod_id

  while IFS= read -r pod_id; do
    [[ -z "$pod_id" ]] && continue

    if pod_has_destructive_label "$pod_id"; then
      printf "%s\n" "$pod_id"
    else
      echo "Pod $(pod_identity "$pod_id") skipped: missing ${DESTRUCTIVE_LABEL_KEY}=${DESTRUCTIVE_LABEL_VALUE}" >&2
    fi
  done < <(list_pods)
}

target_pods_exist() {
  local pod_id

  while IFS= read -r pod_id; do
    [[ -z "$pod_id" ]] && continue
    pod_has_destructive_label "$pod_id" && return 0
  done < <(list_pods)

  return 1
}

stop_container() {
  local container_id="$1"

  if ! "${CRICTL[@]}" stop "$container_id"; then
    echo "WARN: stop failed for container $container_id (continuing)."
  fi
}

stop_pod_containers() {
  local pod_id="$1"
  local container_id
  local filtered_containers=()

  if ! pod_has_destructive_label "$pod_id"; then
    echo "Pod $(pod_identity "$pod_id"): skipped, missing ${DESTRUCTIVE_LABEL_KEY}=${DESTRUCTIVE_LABEL_VALUE}"
    return 0
  fi

  mapfile -t filtered_containers < <(filtered_containers_for_pod "$pod_id")

  if (( ${#filtered_containers[@]} == 0 )); then
    return 0
  fi

  echo "Pod $pod_id: stopping ${#filtered_containers[@]} container..."

  for container_id in "${filtered_containers[@]}"; do
    stop_container "$container_id"
  done

  echo "Pod $pod_id: done"
}

stop_all_pod_containers_once() {
  local pods=()
  local pod_id

  mapfile -t pods < <(list_target_pods)

  if (( ${#pods[@]} == 0 )); then
    echo "No pod with ${DESTRUCTIVE_LABEL_KEY}=${DESTRUCTIVE_LABEL_VALUE} found."
    exit 0
  fi

  for pod_id in "${pods[@]}"; do
    stop_pod_containers "$pod_id"
  done

  echo "Every labelled pod processed"
}

# ==============================================================================
# Main
# ==============================================================================

find_kubelet_pids
init_crictl

if ! target_pods_exist; then
  echo "No pod with ${DESTRUCTIVE_LABEL_KEY}=${DESTRUCTIVE_LABEL_VALUE} found."
  exit 0
fi

pause_kubelet

while :; do
  stop_all_pod_containers_once
  sleep "$INTERVAL"
done
