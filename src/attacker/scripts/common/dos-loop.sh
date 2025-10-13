#!/usr/bin/env bash
set -euo pipefail

NAME_POD="agent"
CRICTL="$1 crictl"
INTERVAL="3"

$1 kill -STOP "$($1 pidof kubelet)"

while :; do
  # Pod list (sandbox) on node
  mapfile -t PODS < <($CRICTL pods -q)

  if [ "${#PODS[@]}" -eq 0 ]; then
    echo "No pod found."
    exit 0
  fi

  for POD in "${PODS[@]}"; do
    # Finding containers belonging to pod
    mapfile -t CIDS < <($CRICTL ps -q --pod "$POD")
    FILTERED=()
    for CID in "${CIDS[@]}"; do
      if [[ $($CRICTL inspect "$CID" 2>/dev/null | jq -r '.status.metadata.name // empty') == "$NAME_POD" ]]; then
        continue
      fi
      FILTERED+=("$CID")
    done

    # If there are not containers in the pod, skip
    if [ "${#FILTERED[@]}" -eq 0 ]; then
      continue
    fi

    echo "Pod ${POD}: stopping ${#FILTERED[@]} container..."
    for CID in "${FILTERED[@]}"; do
      if ! $CRICTL stop "$CID"; then
        echo "WARN: stop failed for container $CID (continuing)."
      fi
    done

    echo "Pod ${POD}: done"
  done

  echo "Every pod stopped"
  sleep "$INTERVAL"
done
