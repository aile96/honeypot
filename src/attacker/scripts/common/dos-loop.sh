#!/usr/bin/env bash
set -euo pipefail

# Configura qui il socket CRI (esempi comuni sotto).
# Di default punta a containerd su /host/var/run/containerd/containerd.sock
RUNTIME_ENDPOINT="${RUNTIME_ENDPOINT:-unix:///host/var/run/containerd/containerd.sock}"
IMAGE_ENDPOINT="${IMAGE_ENDPOINT:-$RUNTIME_ENDPOINT}"

CRICTL="crictl --runtime-endpoint=${RUNTIME_ENDPOINT} --image-endpoint=${IMAGE_ENDPOINT}"

# Durata della pausa in secondi (default 300 = 5 minuti)
DURATION="${1:-300}"

# Verifica supporto pause/unpause
if ! $CRICTL --help 2>/dev/null | grep -qE '\b(pause|unpause)\b'; then
  echo "ERRORE: questo crictl/runtime non espone i comandi 'pause/unpause'."
  echo "Suggerimento: verifica versione di crictl e runtime, oppure usa 'ctr tasks pause/resume' come fallback."
  exit 1
fi

echo "Usando runtime endpoint: ${RUNTIME_ENDPOINT}"
echo "Pausa per ${DURATION} secondi per ogni pod."

# Elenco dei pod (sandbox) sul nodo
mapfile -t PODS < <($CRICTL pods -q)

if [ "${#PODS[@]}" -eq 0 ]; then
  echo "Nessun pod trovato."
  exit 0
fi

for POD in "${PODS[@]}"; do
  # Trova i container appartenenti al pod
  mapfile -t CIDS < <($CRICTL ps -q --pod "$POD")
  # Filtra via l'infra container (il cui nome è 'POD')
  FILTERED=()
  for CID in "${CIDS[@]}"; do
    # Se l'ispezione mostra "name": "POD", salta
    if $CRICTL inspect "$CID" 2>/dev/null | grep -q '"name": "POD"'; then
      continue
    fi
    FILTERED+=("$CID")
  done

  # Se non ci sono container utili nel pod, passa oltre
  if [ "${#FILTERED[@]}" -eq 0 ]; then
    continue
  fi

  echo "Pod ${POD}: metto in pausa ${#FILTERED[@]} container..."
  for CID in "${FILTERED[@]}"; do
    if ! $CRICTL pause "$CID"; then
      echo "WARN: pausa fallita per container $CID (continuo)."
    fi
  done

  echo "In pausa per ${DURATION}s..."
  sleep "${DURATION}"

  echo "Pod ${POD}: riprendo i container..."
  for CID in "${FILTERED[@]}"; do
    if ! $CRICTL unpause "$CID"; then
      echo "WARN: ripresa fallita per container $CID."
    fi
  done

  echo "Pod ${POD}: fatto."
done

echo "Tutti i pod sono stati gestiti."
