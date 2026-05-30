#!/usr/bin/env bash
set -euo pipefail

PIDFILE="${1:-$DATA_PATH/KC5/arp_pids}"
TERM_WAIT_SECONDS="5"

if [[ ! -f "$PIDFILE" ]]; then
  echo "[*] PID file not found, nothing to stop: $PIDFILE"
  exit 0
fi

# Function that tries to stop a PID cleanly
stop_pid() {
  local pid="$1"

  # Sanity check: PID must be numeric
  if ! [[ "$pid" =~ ^[0-9]+$ ]]; then
    echo "[!] Ignore line not numeric: '$pid'"
    return
  fi

  if ! kill -0 "$pid" 2>/dev/null; then
    echo "[*] PID $pid doesn't exist (already killed)."
    return
  fi

  echo "[*] Sending SIGTERM to PID $pid"
  kill -TERM "$pid" 2>/dev/null || true

  # Wait for termination before TERM_WAIT_SECONDS elapses
  local end=$(( SECONDS + TERM_WAIT_SECONDS ))
  while kill -0 "$pid" 2>/dev/null; do
    if (( SECONDS >= end )); then
      echo "[*] PID $pid still alive after ${TERM_WAIT_SECONDS}s, sending SIGKILL"
      kill -KILL "$pid" 2>/dev/null || true
      break
    fi
    sleep 1
  done

  if kill -0 "$pid" 2>/dev/null; then
    echo "[!] PID $pid seems to still be running after SIGKILL." >&2
  else
    echo "[*] PID $pid terminated."
  fi
}

# Read the PID file and stop each PID, one per line
while IFS= read -r line || [[ -n "$line" ]]; do
  # strip spaces
  line="${line#"${line%%[![:space:]]*}"}"
  line="${line%"${line##*[![:space:]]}"}"
  [[ -z "$line" ]] && continue
  stop_pid "$line"
done < "$PIDFILE"

# Remove the PID file
rm -f "$PIDFILE" || true
echo "[*] Every PID has been processed. PID file deleted: $PIDFILE"

exit 0
