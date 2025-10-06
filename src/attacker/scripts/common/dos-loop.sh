#!/usr/bin/env bash
set -euo pipefail

NAME_POD="agent"
CRICTL="$1 crictl"
INTERVAL="3"

$1 kill -STOP "$($1 pidof kubelet)"

while :; do
  # Elenco dei pod (sandbox) sul nodo
  mapfile -t PODS < <($CRICTL pods -q)

  if [ "${#PODS[@]}" -eq 0 ]; then
    echo "Nessun pod trovato."
    exit 0
  fi

  for POD in "${PODS[@]}"; do
    # Trova i container appartenenti al pod
    mapfile -t CIDS < <($CRICTL ps -q --pod "$POD")
    FILTERED=()
    for CID in "${CIDS[@]}"; do
      if [[ $($CRICTL inspect "$CID" 2>/dev/null | jq -r '.status.metadata.name // empty') == "$NAME_POD" ]]; then
        continue
      fi
      FILTERED+=("$CID")
    done

    # Se non ci sono container utili nel pod, passa oltre
    if [ "${#FILTERED[@]}" -eq 0 ]; then
      continue
    fi

    echo "Pod ${POD}: metto in stop ${#FILTERED[@]} container..."
    for CID in "${FILTERED[@]}"; do
      if ! $CRICTL stop "$CID"; then
        echo "WARN: stop fallito per container $CID (continuo)."
      fi
    done

    echo "Pod ${POD}: fatto."
  done

  echo "Tutti i pod sono stati gestiti."
  sleep "$INTERVAL"
done
