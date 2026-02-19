#!/usr/bin/env bash
set -euo pipefail

NAME_POD="agent"
INTERVAL="3"
REMOTE_CMD_RAW="${1:-}"
[[ -n "${REMOTE_CMD_RAW}" ]] || { echo "Usage: $0 '<remote command prefix>'" >&2; exit 1; }

read -r -a REMOTE_CMD <<< "${REMOTE_CMD_RAW}"
CRICTL=( "${REMOTE_CMD[@]}" crictl )
KUBELET_PIDS=()

cleanup() {
  if (( ${#KUBELET_PIDS[@]} > 0 )); then
    "${REMOTE_CMD[@]}" kill -CONT "${KUBELET_PIDS[@]}" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT INT TERM

mapfile -t KUBELET_PIDS < <(
  "${REMOTE_CMD[@]}" pidof kubelet 2>/dev/null \
    | tr ' ' '\n' \
    | sed '/^$/d'
)

if (( ${#KUBELET_PIDS[@]} == 0 )); then
  echo "No kubelet PID found"
  exit 1
fi

"${REMOTE_CMD[@]}" kill -STOP "${KUBELET_PIDS[@]}"

while :; do
  # Pod list (sandbox) on node
  mapfile -t PODS < <("${CRICTL[@]}" pods -q)

  if (( ${#PODS[@]} == 0 )); then
    echo "No pod found."
    exit 0
  fi

  for POD in "${PODS[@]}"; do
    # Finding containers belonging to pod
    mapfile -t CIDS < <("${CRICTL[@]}" ps -q --pod "$POD")
    FILTERED=()
    for CID in "${CIDS[@]}"; do
      if [[ $("${CRICTL[@]}" inspect "$CID" 2>/dev/null | jq -r '.status.metadata.name // empty') == "$NAME_POD" ]]; then
        continue
      fi
      FILTERED+=("$CID")
    done

    # If there are no containers in the pod, skip
    if (( ${#FILTERED[@]} == 0 )); then
      continue
    fi

    echo "Pod ${POD}: stopping ${#FILTERED[@]} container..."
    for CID in "${FILTERED[@]}"; do
      if ! "${CRICTL[@]}" stop "$CID"; then
        echo "WARN: stop failed for container $CID (continuing)."
      fi
    done

    echo "Pod ${POD}: done"
  done

  echo "Every pod stopped"
  sleep "$INTERVAL"
done
