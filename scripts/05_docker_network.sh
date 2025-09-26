#!/bin/bash

CALDERA_SERVER=$1
CALDERA_UNDERLAY=$2
CALDERA_OUTSIDE=$3
CALDERA_CONTROLLER=$4
BRIDGE_NET=$5
ROUTER_NAME=$6
LOAD_GENERATOR=$7
CLUSTER_NAME=$8
KIND_NET="kind"

exec_in_container() {
  local CID="$1"; shift
  # Skip router
  local NAME
  NAME="$(docker inspect -f '{{.Name}}' "$CID" | sed 's#^/##')"
  [[ "$NAME" == "$ROUTER_NAME" ]] && return 0

  local RUNNING
  RUNNING="$(docker inspect -f '{{.State.Running}}' "$CID" 2>/dev/null || echo false)"
  [[ "$RUNNING" == "true" ]] || { echo "   ${NAME} non è in esecuzione, salto."; return 0; }

  if docker exec "$CID" sh -c "command -v ip >/dev/null 2>&1"; then
    docker exec "$CID" sh -c "$*"
  else
    if echo "$*" | grep -q "^ip route replace "; then
      local SUBNET GW
      SUBNET="$(echo "$*" | awk '{print $4}')"
      GW="$(echo "$*" | awk '{print $6}')"
      docker exec "$CID" sh -c "command -v route >/dev/null 2>&1 && (route del -net $SUBNET gw $GW 2>/dev/null || true; route add -net $SUBNET gw $GW)"
    else
      echo "   Nessun 'ip' nel container e comando non riconosciuto per fallback."
      return 1
    fi
  fi
}

list_running_on_net() {
  local NET="$1"
  docker ps --filter "network=$NET" --format '{{.ID}}'
}

# Verifica rete kind
KIND_SUBNET="$(docker network inspect "$KIND_NET" | jq -r '.[0].IPAM.Config[] | select(.Subnet  | test("\\.")) | .Subnet' | head -n1 || true)"
if [[ -z "${KIND_SUBNET}" || "${KIND_SUBNET}" == "<no value>" ]]; then
  err "Rete Docker '$KIND_NET' non trovata. Avvia il cluster kind prima di procedere."
  exit 1
fi

# Verifica rete esterna
BRIDGE_SUBNET="$(docker network inspect "${BRIDGE_NET}" -f '{{(index .IPAM.Config 0).Subnet}}' 2>/dev/null || true)"
if [[ -z "${BRIDGE_SUBNET}" || "${BRIDGE_SUBNET}" == "<no value>" ]]; then
  log "Creazione della rete '${BRIDGE_NET}'..."
  docker network create --driver bridge "$BRIDGE_NET"
  BRIDGE_SUBNET="$(docker network inspect "${BRIDGE_NET}" -f '{{(index .IPAM.Config 0).Subnet}}')"
else
  warn "Rete '${BRIDGE_NET}' già esistente (subnet: ${BRIDGE_SUBNET})"
fi

# Avvio router
if docker ps -a --format '{{.Names}}' | grep -q "^$ROUTER_NAME$"; then
  warn "Container $ROUTER_NAME già esistente."
else
  log "Avvio del Docker $ROUTER_NAME ..."
  docker run -d --name "$ROUTER_NAME" \
    --network $KIND_NET \
    --cap-add NET_ADMIN --cap-add NET_RAW \
    --sysctl net.ipv4.ip_forward=1 \
    --sysctl net.ipv4.conf.all.rp_filter=0 \
    --sysctl net.ipv4.conf.default.rp_filter=0 \
    --restart unless-stopped \
    registry:5000/router:2.0.2
  docker network connect "$BRIDGE_NET" "$ROUTER_NAME" || true
fi

# Recupero IP dinamici del router
KIND_ROUTER_IP="$(docker inspect -f '{{.NetworkSettings.Networks.'"$KIND_NET"'.IPAddress}}' "$ROUTER_NAME")"
BRIDGE_ROUTER_IP="$(docker inspect -f '{{.NetworkSettings.Networks.'"$BRIDGE_NET"'.IPAddress}}' "$ROUTER_NAME")"

# Avvio Caldera server
if docker ps -a --format '{{.Names}}' | grep -q "^$CALDERA_SERVER$"; then
  warn "Container $CALDERA_SERVER già esistente."
else
  log "Avvio del Docker $CALDERA_SERVER ..."
  docker run -d --name "$CALDERA_SERVER" \
    --network "$KIND_NET" --cap-add NET_ADMIN \
    registry:5000/caldera:2.0.2
fi

# Avvio Caldera controller
if docker ps -a --format '{{.Names}}' | grep -q "^$CALDERA_CONTROLLER$"; then
  warn "Container $CALDERA_CONTROLLER già esistente."
else
  log "Avvio del Docker $CALDERA_CONTROLLER ..."
  docker run -d --name "$CALDERA_CONTROLLER" \
    --network "$KIND_NET" --cap-add NET_ADMIN \
    registry:5000/caldera-controller:2.0.2
fi

# Avvio Load Generator
if docker ps -a --format '{{.Names}}' | grep -q "^$LOAD_GENERATOR$"; then
  warn "Container $LOAD_GENERATOR già esistente."
else
  log "Avvio del Docker $LOAD_GENERATOR ..."
  docker run -d --name "$LOAD_GENERATOR" \
    --network "$BRIDGE_NET" --cap-add NET_ADMIN \
    registry:5000/load-generator:2.0.2
fi

# Recupero IP dinamico del Caldera server
CALDERA_IP="$(docker inspect -f '{{.NetworkSettings.Networks.'"$KIND_NET"'.IPAddress}}' "$CALDERA_SERVER")"

# Avvio Caldera underlay attacker
if docker ps -a --format '{{.Names}}' | grep -q "^$CALDERA_UNDERLAY$"; then
  warn "Container $CALDERA_UNDERLAY già esistente."
else
  log "Avvio del Docker $CALDERA_UNDERLAY ..."
  docker run -d --name "$CALDERA_UNDERLAY" --network $KIND_NET \
    -v /var/run/docker.sock:/var/run/docker.sock -e "GROUP=underlay" \
    --hostname "$CALDERA_UNDERLAY" --privileged \
    registry:5000/attacker:2.0.2
fi

API_SERVER_IP="$(docker inspect -f '{{.NetworkSettings.Networks.'"$KIND_NET"'.IPAddress}}' "$CLUSTER_NAME-control-plane")"
SMTP_IP_ADDR="$(docker inspect -f '{{.NetworkSettings.Networks.'"$KIND_NET"'.IPAddress}}' "$CLUSTER_NAME-worker")"

# Avvio Caldera outsider attacker
if docker ps -a --format '{{.Names}}' | grep -q "^$CALDERA_OUTSIDE$"; then
  warn "Container $CALDERA_OUTSIDE già esistente."
else
  log "Avvio del Docker $CALDERA_OUTSIDE ..."
  docker run -d --name "$CALDERA_OUTSIDE" --network "$BRIDGE_NET" \
    --hostname "$CALDERA_OUTSIDE" -e "API_SERVER=https://$API_SERVER_IP:6443" \
    -e "GROUP=outside" -e "WAIT=1" -e "SMTP_IP=$SMTP_IP_ADDR" --cap-add NET_ADMIN \
    registry:5000/attacker:2.0.2
fi

CALDERA_OUT_IP="$(docker inspect -f '{{.NetworkSettings.Networks.'"$BRIDGE_NET"'.IPAddress}}' "$CALDERA_OUTSIDE")"

RET=0

# Route per container kind
log ">>> Configuro i container su kind per raggiungere $BRIDGE_SUBNET via $KIND_ROUTER_IP"
for CID in $(list_running_on_net "$KIND_NET"); do
  NAME="$(docker inspect -f '{{.Name}}' "$CID" | sed 's#^/##')"
  log " - $NAME"
  if ! exec_in_container "$CID" "ip route replace $BRIDGE_SUBNET via $KIND_ROUTER_IP && echo '$CALDERA_OUT_IP $CALDERA_OUTSIDE' >> /etc/hosts"; then
    warn "   fallito su $NAME"
    RET=1
  fi
done

# Route per container bridge
log ">>> Configuro i container su '$BRIDGE_NET' per raggiungere $KIND_SUBNET via $BRIDGE_ROUTER_IP"
for CID in $(list_running_on_net "$BRIDGE_NET"); do
  NAME="$(docker inspect -f '{{.Name}}' "$CID" | sed 's#^/##')"
  log " - $NAME"
  if ! exec_in_container "$CID" "ip route replace $KIND_SUBNET via $BRIDGE_ROUTER_IP && echo '$CALDERA_IP $CALDERA_SERVER' >> /etc/hosts"; then
    warn "   fallito su $NAME"
    RET=1
  fi
done

if [[ "$RET" -eq 0 ]]; then
  log "Completato: rotte aggiornate su entrambe le reti."
else
  warn "Completato con alcuni errori. Vedi i messaggi sopra."
fi
