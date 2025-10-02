#!/usr/bin/env bash
set -euo pipefail

# Interfacce (compose attacca le reti in quest'ordine)
IFACE_A="${IFACE_A:-eth0}"
IFACE_B="${IFACE_B:-eth1}"
NAT="${NAT:-0}"
API_SERVER="${API_SERVER:-kind-cluster-control-plane}"
WORKER="${WORKER:-kind-cluster-worker}"
APP_NAME="${APP_NAME:-fantasticshop}"
LOAD_BALANCER_IP="${LOAD_BALANCER_IP:-172.18.0.200}"
REFRESH_SEC="${REFRESH_SEC:-300}"
B2A_CHAIN="B2A_ALLOW"
DNS_DOCKER=$(grep -m1 '^nameserver' /etc/resolv.conf | awk '{print $2}')

# -------------------------
# Funzioni utili
# -------------------------
reverse_ptr() {
  local ip="$1"
  IFS='.' read -r a b c d <<<"$ip" || return 1
  echo "${d}.${c}.${b}.${a}.in-addr.arpa"
}

# Funzione che ricostruisce le regole consentite
rebuild_b2a_chain() {
  iptables -N "$B2A_CHAIN" 2>/dev/null || true
  iptables -F "$B2A_CHAIN"

  # 1) IP fisso (se impostato)
  if [[ -n "${LOAD_BALANCER_IP:-}" ]]; then
    iptables -A "$B2A_CHAIN" -d "$LOAD_BALANCER_IP" -j ACCEPT
  fi

  # 2) A-record correnti dell'API_SERVER (se impostato)
  if [[ -n "${API_SERVER:-}" ]]; then
    mapfile -t API_IPS < <( { getent ahostsv4 "$API_SERVER" || true; } | awk '{print $1}' | sort -u )
    for ip in "${API_IPS[@]}"; do
      [[ -n "$ip" ]] && iptables -A "$B2A_CHAIN" -d "$ip" -j ACCEPT
    done
  fi

  # Default: tutto il resto DROP
  iptables -A "$B2A_CHAIN" -j DROP

  echo "[entrypoint] Ricostruita catena ${B2A_CHAIN} (LB=${LOAD_BALANCER_IP}, API=${API_SERVER})"
}

# -------------------------
# Abilita forwarding e pulisci tabelle
# -------------------------
sysctl -w net.ipv4.ip_forward=1 >/dev/null
# rp_filter off (evita drop su percorsi asimmetrici)
sysctl -w net.ipv4.conf.all.rp_filter=0 >/dev/null || true
sysctl -w net.ipv4.conf.default.rp_filter=0 >/dev/null || true
sysctl -w "net.ipv4.conf.${IFACE_A}.rp_filter=0" >/dev/null || true
sysctl -w "net.ipv4.conf.${IFACE_B}.rp_filter=0" >/dev/null || true

iptables -t filter -F || true
iptables -t filter -X || true
iptables -t nat -F || true
iptables -t nat -X || true

# Policy di default: DROP sul forward (poi apriamo ciò che serve)
iptables -P INPUT ACCEPT
iptables -P OUTPUT ACCEPT
iptables -P FORWARD DROP

# Consenti ESTABLISHED/RELATED in entrambe le direzioni
iptables -A FORWARD -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT

# eth0 -> eth1: tutto libero
iptables -A FORWARD -i "$IFACE_A" -o "$IFACE_B" -j ACCEPT

# -------------------------
# ipset con destinazioni CONSENTITE da eth1 -> eth0
# -------------------------
# Prima build
rebuild_b2a_chain
iptables -A FORWARD -i "$IFACE_B" -o "$IFACE_A" -j "$B2A_CHAIN"

# NAT opzionale
if [[ "$NAT" = "1" ]]; then
  iptables -t nat -A POSTROUTING -o "$IFACE_A" -j MASQUERADE
  iptables -t nat -A POSTROUTING -o "$IFACE_B" -j MASQUERADE
  echo "[entrypoint] NAT MASQUERADE attivo su ${IFACE_A} e ${IFACE_B}"
else
  echo "[entrypoint] Routing puro senza NAT"
fi

# -------------------------
# Genera i file di config dnsmasq dai template
# -------------------------
export API_SERVER APP_NAME LOAD_BALANCER_IP WORKER DNS_DOCKER
envsubst < /etc/dnsmasq.conf.tmpl > /etc/dnsmasq.conf
envsubst < /etc/dnsmasq-eth1.conf.tmpl > /etc/dnsmasq-eth1.conf

# Validazione config
dnsmasq --test --conf-file=/etc/dnsmasq.conf
dnsmasq --test --conf-file=/etc/dnsmasq-eth1.conf

# -------------------------
# Loop di refresh del set (segue i cambi DNS di API_SERVER)
# -------------------------
(
  while sleep "${REFRESH_SEC}"; do
    rebuild_b2a_chain || true
  done
) &

# -------------------------
# Avvia due istanze dnsmasq
# -------------------------
# Requisito nei config: bind-interfaces + interface=<if>
# Calcola gli IP delle interfacce
IP_A="$(ip -4 -o addr show dev "$IFACE_A" | awk '{print $4}' | cut -d/ -f1)"
IP_B="$(ip -4 -o addr show dev "$IFACE_B" | awk '{print $4}' | cut -d/ -f1)"

# Istanza ETH0 (libera) — bind SOLO su IP_A
dnsmasq --keep-in-foreground \
  --conf-file=/etc/dnsmasq.conf \
  --conf-dir= \                      # disabilita /etc/dnsmasq.d/*
  --bind-interfaces \
  --listen-address="$IP_A" \
  --except-interface=lo \
  --pid-file=/run/dnsmasq-eth0.pid \
  --log-facility=- &
DNS0=$!

# Istanza ETH1 (ristretta) — bind SOLO su IP_B
dnsmasq --keep-in-foreground \
  --conf-file=/etc/dnsmasq-eth1.conf \
  --conf-dir= \                      # disabilita /etc/dnsmasq.d/*
  --bind-interfaces \
  --listen-address="$IP_B" \
  --except-interface=lo \
  --pid-file=/run/dnsmasq-eth1.pid \
  --log-facility=- &
DNS1=$!


# Shutdown pulito
trap 'kill $DNS0 $DNS1 2>/dev/null || true; wait; exit 0' SIGINT SIGTERM

# Se una istanza muore, chiudi tutto
wait -n "$DNS0" "$DNS1"
kill "$DNS0" "$DNS1" 2>/dev/null || true
wait