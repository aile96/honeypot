#!/usr/bin/env bash
set -euo pipefail

# ===== Parametri =====
KIND_NET="kind"

# ===== Utility host =====
log(){ printf "\033[1;36m[INFO]\033[0m %s\n" "$*"; }
warn(){ printf "\033[1;33m[WARN]\033[0m %s\n" "$*"; }
err(){ printf "\033[1;31m[ERR ]\033[0m %s\n" "$*" >&2; }
die()   { echo -e "[ERROR] $*" >&2; exit 1; }

net_subnet() {
  docker network inspect "$1" \
  --format '{{range .IPAM.Config}}{{if .Subnet}}{{.Subnet}}{{"\n"}}{{end}}{{end}}' \
  | grep '\.' | head -n1
}
ip_on() {
  docker inspect -f "{{with index .NetworkSettings.Networks \"$2\"}}{{.IPAddress}}{{end}}" "$1" 2>/dev/null || true
}
list_on() {
  docker ps --filter "network=$1" --format '{{.ID}}'
}
is_running() {
  docker inspect -f '{{.State.Running}}' "$1" 2>/dev/null | grep -qi true
}

# ===== Esecutori robusti dentro container =====
retry_exec() {
  # retry_exec <cid> <shell-cmd>
  local cid="$1"; shift
  local cmd="$*"
  local tries=(0.5 1 2 3 5) i=0
  while :; do
    if docker exec "$cid" sh -lc "$cmd" >/dev/null 2>&1; then
      return 0
    fi
    [ $i -ge ${#tries[@]} ] && return 1
    sleep "${tries[$i]}"; i=$((i+1))
  done
}

add_route() {
  # add_route <cid> <dst_subnet> <via_ip>
  local cid="$1" dst="$2" via="$3"
  # 1) ip(8) classico
  if retry_exec "$cid" "command -v ip >/dev/null 2>&1 && ip -4 route replace $dst via $via"; then
    return 0
  fi
  # 2) busybox ip
  if retry_exec "$cid" "busybox ip -4 route replace $dst via $via"; then
    return 0
  fi
  # 3) route (net-tools)
  if retry_exec "$cid" "command -v route >/dev/null 2>&1 && (route del -net $dst gw $via 2>/dev/null || true; route add -net $dst gw $via)"; then
    return 0
  fi
  return 1
}

set_resolver() {
  # set_resolver <cid> <dns_ip>
  local cid="$1" dns_ip="$2"
  # se /etc/resolv.conf è scrivibile, usa echo >; altrimenti prova temp+mv
  if retry_exec "$cid" "test -w /etc/resolv.conf"; then
    retry_exec "$cid" "printf 'nameserver %s\n' '$dns_ip' > /etc/resolv.conf" && return 0
  fi
  retry_exec "$cid" "tmp=\$(mktemp /tmp/resolv.XXXXXX) && printf 'nameserver %s\n' '$dns_ip' > \$tmp && cp \$tmp /etc/resolv.conf && rm -f \$tmp" && return 0
  return 1
}

## ===== 1) Installo su caldera il pacchetto iproute2 =====
#docker exec -u root $CALDERA_SERVER bash -lc \
#  "apt-get update && apt-get install -y --no-install-recommends iproute2 && rm -rf /var/lib/apt/lists/*"
#
## ===== 2) Scoperta parametri dinamici =====
#log "Leggo subnet..."
#KIND_SUBNET="$(net_subnet "$KIND_NET")"
#BRIDGE_SUBNET="$(net_subnet "$BRIDGE_NET")"
#[ -n "$KIND_SUBNET" ] || die "Subnet rete '$KIND_NET' non trovata."
#[ -n "$BRIDGE_SUBNET" ] || die "Subnet rete '$BRIDGE_NET' non trovata."
#
#log "IP router su entrambe le reti..."
#KIND_ROUTER_IP="$(ip_on "$ROUTER_NAME" "$KIND_NET")"
#BRIDGE_ROUTER_IP="$(ip_on "$ROUTER_NAME" "$BRIDGE_NET")"
#[ -n "$KIND_ROUTER_IP" ] || die "Router '$ROUTER_NAME' non connesso a '$KIND_NET'."
#[ -n "$BRIDGE_ROUTER_IP" ] || die "Router '$ROUTER_NAME' non connesso a '$BRIDGE_NET'."
#
#log "KIND_SUBNET=$KIND_SUBNET | BRIDGE_SUBNET=$BRIDGE_SUBNET"
#log "KIND_ROUTER_IP=$KIND_ROUTER_IP | BRIDGE_ROUTER_IP=$BRIDGE_ROUTER_IP"
#
## ===== 3) Applica rotte + resolver, con robustezza & report =====
#FAILED_ROUTES=()
#FAILED_RESOLV=()
#
#log "Rotte: '$KIND_NET' -> $BRIDGE_SUBNET via $KIND_ROUTER_IP / Resolver -> $KIND_ROUTER_IP"
#for cid in $(list_on "$KIND_NET"); do
#  name="$(docker inspect -f '{{.Name}}' "$cid" | sed 's#^/##')"
#  # skip router/dns
#  [[ "$name" == "$ROUTER_NAME" ]] && continue
#  is_running "$cid" || { warn "  - $name (non in esecuzione)"; continue; }
#  printf "  - %-24s" "$name"
#  if add_route "$cid" "$BRIDGE_SUBNET" "$KIND_ROUTER_IP"; then
#    echo " route OK"
#  else
#    echo " route FAIL"; FAILED_ROUTES+=("$name(kind)")
#  fi
#  if set_resolver "$cid" "$KIND_ROUTER_IP"; then
#    echo " dns OK"
#  else
#    echo " dns FAIL"; FAILED_RESOLV+=("$name")
#  fi
#done
#
#log "Rotte: '$BRIDGE_NET' -> $KIND_SUBNET via $BRIDGE_ROUTER_IP / Resolver -> $BRIDGE_ROUTER_IP"
#for cid in $(list_on "$BRIDGE_NET"); do
#  name="$(docker inspect -f '{{.Name}}' "$cid" | sed 's#^/##')"
#  [[ "$name" == "$ROUTER_NAME" ]] && continue
#  is_running "$cid" || { warn "  - $name (non in esecuzione)"; continue; }
#  printf "  - %-24s" "$name"
#  if add_route "$cid" "$KIND_SUBNET" "$BRIDGE_ROUTER_IP"; then
#    echo " route OK"
#  else
#    echo " route FAIL"; FAILED_ROUTES+=("$name($BRIDGE_NET)")
#  fi
#  if set_resolver "$cid" "$BRIDGE_ROUTER_IP"; then
#    echo " dns OK"
#  else
#    echo " dns FAIL"; FAILED_RESOLV+=("$name")
#  fi
#done
#
## ===== 4) Riepilogo =====
#echo
#if [ ${#FAILED_ROUTES[@]} -eq 0 ] && [ ${#FAILED_RESOLV[@]} -eq 0 ]; then
#  log "Completato: rotte e DNS configurati su tutti i container."
#else
#  warn "Completato con errori:"
#  [ ${#FAILED_ROUTES[@]} -gt 0 ] && { echo " - Route fallite:"; printf "   • %s\n" "${FAILED_ROUTES[@]}"; }
#  [ ${#FAILED_RESOLV[@]} -gt 0 ] && { echo " - DNS non impostato:"; printf "   • %s\n" "${FAILED_RESOLV[@]}"; }
#  echo "Suggerimenti:"
#  echo " • Alcune immagini non hanno 'ip' né 'route': valuta di aggiungere NET_ADMIN o usare immagini con iproute2/net-tools."
#  echo " • Se /etc/resolv.conf è read-only, considera di usare 'dns:' in compose per quei servizi."
#fi
