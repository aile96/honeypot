#!/bin/bash

CALDERA_SERVER=$1
CALDERA_ATTACKER=$2
CALDERA_IP=$3

# Verifica che la rete kind esista
if ! docker network inspect kind >/dev/null 2>&1; then
  err "Rete Docker 'kind' non trovata. Avvia il cluster kind prima di procedere."
  exit 1
fi

# Avvio Caldera attacker
if docker ps -a --format '{{.Names}}' | grep -q "^$CALDERA_ATTACKER$"; then
  warn "Container $CALDERA_ATTACKER già esistente."
else
  log "Avvio del Docker $CALDERA_ATTACKER ..."
  docker run -d --name $CALDERA_ATTACKER --network kind \
    -v /var/run/docker.sock:/var/run/docker.sock \
    localhost:5000/attacker:2.0.2
fi

docker cp corp-proxy.crt "$CALDERA_ATTACKER":/usr/local/share/ca-certificates/corp-proxy.crt

docker exec -it "$CALDERA_ATTACKER" sh -ceu '
  apt-get update && apt-get install -y ca-certificates && rm -rf /var/lib/apt/lists/*
  update-ca-certificates
'

docker restart "$CALDERA_ATTACKER"

if docker network inspect kind | grep -q "\"Name\": \"$CALDERA_ATTACKER\""; then
  log "$CALDERA_ATTACKER già connesso alla rete kind."
else
  warn "Connessione di $CALDERA_ATTACKER alla rete Kind..."
  docker network connect kind "$CALDERA_ATTACKER" || true
fi

# Avvio Caldera attacker
if docker ps -a --format '{{.Names}}' | grep -q "^$CALDERA_SERVER$"; then
  warn "Container $CALDERA_SERVER già esistente."
else
  log "Avvio del Docker $CALDERA_SERVER ..."
  docker run -d --name $CALDERA_SERVER --network kind \
    --ip "$CALDERA_IP" localhost:5000/caldera:2.0.2
fi

if docker network inspect kind | grep -q "\"Name\": \"$CALDERA_SERVER\""; then
  log "$CALDERA_SERVER già connesso alla rete kind."
else
  warn "Connessione di $CALDERA_SERVER alla rete Kind..."
  docker network connect kind "$CALDERA_SERVER" || true
fi

### === Recupero IP del container sulla rete kind ===
CALDERA_IP="$(docker inspect -f '{{(index .NetworkSettings.Networks "'"kind"'").IPAddress}}' "$CALDERA_SERVER")"
if [[ -z "$CALDERA_IP" ]]; then
  err "Impossibile ottenere l'IP del container su rete kind"; exit 1
fi
if [ "$CALDERA_IP" != "$CALDERA_IP" ]; then
  err "Errore: CALDERA_IP ($CALDERA_IP) è diverso da CALDERA_IP ($CALDERA_IP)" >&2
  exit 1
fi






NS="kube-system"
CM="kube-proxy"
TMP_DIR="$(mktemp -d)"
CONF_YAML="${TMP_DIR}/cm.yaml"
CONF_FILE="${TMP_DIR}/config.conf"
MODE="--masq-all"   # --cluster-cidr (auto) | --masq-all

usage() {
  cat <<EOF
Usage:
  $0 --cluster-cidr     # imposta clusterCIDR in kube-proxy (auto-detect dal podCIDR dei nodi)
  $0 --masq-all         # abilita iptables.masqueradeAll: true in kube-proxy
Note: richiede kubectl configurato sul cluster kind corrente.
EOF
}

[[ -z "${MODE}" ]] && { usage; exit 1; }
if [[ "${MODE}" != "--cluster-cidr" && "${MODE}" != "--masq-all" ]]; then
  usage; exit 1
fi

echo ">> Scarico la ConfigMap ${CM} in ${NS}…"
kubectl -n "${NS}" get cm "${CM}" -o yaml > "${CONF_YAML}"

echo ">> Estraggo il file di configurazione kube-proxy (data.config.conf)…"
kubectl -n "${NS}" get cm "${CM}" -o jsonpath='{.data.config\.conf}' > "${CONF_FILE}"

echo ">> Salvo un backup della config originale in ${CONF_FILE}.bak"
cp "${CONF_FILE}" "${CONF_FILE}.bak"

if [[ "${MODE}" == "--cluster-cidr" ]]; then
  echo ">> Rilevo il podCIDR dei nodi…"
  PODCIDR=$(kubectl get nodes -o jsonpath='{range .items[*]}{.spec.podCIDR}{" "}{end}' | awk '{for(i=1;i<=NF;i++) if($i!="") {print $i; exit}}')
  if [[ -z "${PODCIDR}" ]]; then
    echo "!! Non sono riuscito a leggere .spec.podCIDR dai nodi. Userò default kind: 10.244.0.0/16"
    PODCIDR="10.244.0.0/16"
  fi
  echo ">> userò clusterCIDR=${PODCIDR}"

  if grep -qE '^[[:space:]]*clusterCIDR:' "${CONF_FILE}"; then
    echo ">> clusterCIDR già presente: aggiorno il valore…"
    sed -E -i "s|^([[:space:]]*clusterCIDR:).*|\1 ${PODCIDR}|" "${CONF_FILE}"
  else
    echo ">> clusterCIDR assente: lo inserisco dopo 'kind: KubeProxyConfiguration'…"
    # Inserisce clusterCIDR con indentazione a 0
    awk -v cidr="${PODCIDR}" '
      {print}
      $0 ~ /^kind:[[:space:]]*KubeProxyConfiguration/ && !ins {print "clusterCIDR: " cidr; ins=1}
    ' "${CONF_FILE}" > "${CONF_FILE}.new"
    mv "${CONF_FILE}.new" "${CONF_FILE}"
  fi

elif [[ "${MODE}" == "--masq-all" ]]; then
  echo ">> Abilito iptables.masqueradeAll: true"
  if grep -qE '^[[:space:]]*iptables:[[:space:]]*$' "${CONF_FILE}"; then
    # c'è la sezione iptables:, assicuriamo masqueradeAll: true dentro la sezione
    awk '
      BEGIN{in_ip=0}
      /^[[:space:]]*iptables:[[:space:]]*$/ {print; in_ip=1; next}
      in_ip==1 && /^[^[:space:]]/ { # uscita dalla sezione (nuova chiave top-level)
        print "  masqueradeAll: true"
        in_ip=0
      }
      {print}
      END{
        if(in_ip==1){
          print "  masqueradeAll: true"
        }
      }
    ' "${CONF_FILE}" > "${CONF_FILE}.new"
    mv "${CONF_FILE}.new" "${CONF_FILE}"
  else
    echo ">> Sezione iptables: assente; la aggiungo con masqueradeAll: true"
    # Aggiunge blocco iptables: a fine file
    printf "\niptables:\n  masqueradeAll: true\n" >> "${CONF_FILE}"
  fi

  # Se esiste una riga masqueradeAll: false, forziamo a true
  sed -E -i 's/^([[:space:]]*masqueradeAll:)[[:space:]]*false/\1 true/' "${CONF_FILE}"
fi

echo ">> Ricreo la ConfigMap con la config aggiornata…"
kubectl create configmap "${CM}" -n "${NS}" \
  --from-file=config.conf="${CONF_FILE}" \
  -o yaml --dry-run=client | kubectl apply -f -

echo ">> Riavvio kube-proxy (DaemonSet)…"
kubectl -n "${NS}" rollout restart ds kube-proxy

echo ">> Attendo che tutti i pod di kube-proxy siano in Running…"
kubectl -n "${NS}" rollout status ds/kube-proxy --timeout=120s

echo ">> Fatto. Prova ora dal tuo namespace (es. web):"
echo "kubectl -n web run test --rm -it --image=busybox:1.36 --restart=Never -- sh"
echo "# dentro il pod:"
echo "wget -O- http://caldera:8888 || nc -vz caldera 8888"