#!/usr/bin/env bash
set -euo pipefail

PIDFILE="${1:-$DATA_PATH/KC5/arp_pids}"
TERM_WAIT_SECONDS="5"

if [[ ! -f "$PIDFILE" ]]; then
  echo "PID file non trovato: $PIDFILE" >&2
  exit 1
fi

# Funzione che prova a terminare un PID in modo pulito
stop_pid() {
  local pid="$1"

  # sanity: PID deve essere numerico
  if ! [[ "$pid" =~ ^[0-9]+$ ]]; then
    echo "[!] Ignoro riga non numerica: '$pid'"
    return
  fi

  if ! kill -0 "$pid" 2>/dev/null; then
    echo "[*] PID $pid non esiste (già terminato)."
    return
  fi

  echo "[*] Inoltro SIGTERM a PID $pid"
  kill -TERM "$pid" 2>/dev/null || true

  # aspetta che termini entro TERM_WAIT_SECONDS
  local end=$(( SECONDS + TERM_WAIT_SECONDS ))
  while kill -0 "$pid" 2>/dev/null; do
    if (( SECONDS >= end )); then
      echo "[*] PID $pid ancora vivo dopo ${TERM_WAIT_SECONDS}s, invio SIGKILL"
      kill -KILL "$pid" 2>/dev/null || true
      break
    fi
    sleep 0.25
  done

  if kill -0 "$pid" 2>/dev/null; then
    echo "[!] PID $pid sembra ancora in esecuzione dopo KILL." >&2
  else
    echo "[*] PID $pid terminato."
  fi
}

# Legge il PIDFILE e ferma ogni PID (uno per riga)
while IFS= read -r line || [[ -n "$line" ]]; do
  # strip spaces
  line="${line#"${line%%[![:space:]]*}"}"
  line="${line%"${line##*[![:space:]]}"}"
  [[ -z "$line" ]] && continue
  stop_pid "$line"
done < "$PIDFILE"

# opzionale: rimuovi il pidfile
rm -f "$PIDFILE" || true
echo "[*] Tutti i PID processati. PID file rimosso: $PIDFILE"

exit 0