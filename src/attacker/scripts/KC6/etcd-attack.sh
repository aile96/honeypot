#!/usr/bin/env bash
set -euo pipefail

### === Parametri ===
APISERVER_IMAGE="${APISERVER_IMAGE:-registry.k8s.io/kube-apiserver:v1.30.0}"
HOST_BIND_PORT="${HOST_BIND_PORT:-16443}"       # porta HTTPS esposta dall'APIServer effimero sul container orchestratore
NAME="${NAME:-k8s-apiserver-ephem}"
CERT_DIR="${CERT_DIR:-$(pwd)/apiserver-certs}"
FORWARD_LOCAL_PORT="${FORWARD_LOCAL_PORT:-2379}"   # porta locale nel container orchestratore dove ascolta socat
FORWARD_TARGET_HOST="${FORWARD_TARGET_HOST:-kind-cluster-control-plane}"
FORWARD_TARGET_PORT="${FORWARD_TARGET_PORT:-12379}" # porta HTTP *interna* di etcd nel nodo kind

### === Helper: installa dipendenze minime (socat, curl, jq, xxd) ===
install_if_missing() {
  local bin="$1" apk_pkg="$2" apt_pkg="$3"
  if ! command -v "$bin" >/dev/null 2>&1; then
    if command -v apk >/dev/null 2>&1; then
      apk add --no-cache "$apk_pkg"
    elif command -v apt-get >/dev/null 2>&1; then
      apt-get update -y
      DEBIAN_FRONTEND=noninteractive apt-get install -y "$apt_pkg"
    else
      echo ">> ERRORE: manca $bin e non ho apk/apt-get per installarlo" >&2
      exit 1
    fi
  fi
}
install_if_missing socat socat socat
install_if_missing curl  curl  curl
install_if_missing jq    jq    jq
install_if_missing xxd   xxd   xxd

### === Avvia forward TCP: orchestratore :2379 -> kind-cluster-control-plane:12379 ===
if ! ss -lnt | awk '{print $4}' | grep -q ":${FORWARD_LOCAL_PORT}$"; then
  echo ">> Avvio socat: :${FORWARD_LOCAL_PORT} -> ${FORWARD_TARGET_HOST}:${FORWARD_TARGET_PORT}"
  socat "TCP-LISTEN:${FORWARD_LOCAL_PORT},fork,reuseaddr" "TCP:${FORWARD_TARGET_HOST}:${FORWARD_TARGET_PORT}" &
  SOCAT_PID=$!
  trap '[[ -n "${SOCAT_PID:-}" ]] && kill ${SOCAT_PID} >/dev/null 2>&1 || true' EXIT
else
  echo ">> Socat già in ascolto su :${FORWARD_LOCAL_PORT}"
fi

# Test rapido del forward dal container orchestratore
echo ">> Test forward etcd (dal container orchestratore)..."
for i in {1..10}; do
  if curl -sf "http://127.0.0.1:${FORWARD_LOCAL_PORT}/health" >/dev/null; then
    break
  fi
  sleep 1
  [[ $i -eq 10 ]] && { echo "!! etcd non risponde via forward"; exit 1; }
done
curl -s "http://127.0.0.1:${FORWARD_LOCAL_PORT}/health" || true

### === Scopri l’IP gateway del demone Docker interno (visto dai suoi container) ===
echo ">> Ricavo l'IP gateway visto dai container del demone *interno*..."
docker pull --quiet busybox:latest >/dev/null
INNER_BRIDGE_IP="$(docker run --rm busybox sh -c "ip route | awk '/default/ {print \$3}'")"
echo ">> INNER_BRIDGE_IP: ${INNER_BRIDGE_IP}"

# Verifica dal punto di vista di un container del demone interno
echo ">> Verifica che i container interni vedano il forward:"
docker run --rm busybox sh -c "wget -qO- http://${INNER_BRIDGE_IP}:${FORWARD_LOCAL_PORT}/health" || {
  echo "!! I container del demone interno NON vedono ${INNER_BRIDGE_IP}:${FORWARD_LOCAL_PORT}/health" >&2
  exit 1
}

### === Cert e chiavi “usa e getta” ===
rm -rf "$CERT_DIR"; mkdir -p "$CERT_DIR"
docker run --rm -v "$CERT_DIR":/out alpine:3.20 sh -c '
  set -e
  apk add --no-cache openssl >/dev/null
  # serving cert per HTTPS
  openssl req -x509 -newkey rsa:2048 -nodes -sha256 -days 1 \
    -subj "/CN=localhost" \
    -keyout /out/tls.key -out /out/tls.crt >/dev/null
  # chiavi per ServiceAccount JWT (privata + pubblica)
  openssl genrsa -out /out/sa.key 2048 >/dev/null 2>&1
  openssl rsa -in /out/sa.key -pubout -out /out/sa.pub >/dev/null 2>&1
'

### === Token statico (admin in system:masters) ===
TOK="$(head -c16 /dev/urandom | xxd -p)"
echo "${TOK},admin,uid-admin,system:masters" > "${CERT_DIR}/tokens.csv"
echo ">> Bearer token (salvato anche in ${CERT_DIR}/token.txt): ${TOK}" | tee "${CERT_DIR}/token.txt" >/dev/null

### === Avvia kube-apiserver effimero (demone Docker interno) ===
echo ">> Avvio kube-apiserver effimero..."
docker rm -f "$NAME" >/dev/null 2>&1 || true
docker run -d --name "$NAME" \
  -p "127.0.0.1:${HOST_BIND_PORT}:6443" \
  -v "$CERT_DIR":/certs:ro \
  "$APISERVER_IMAGE" \
  kube-apiserver \
    --secure-port=6443 \
    --tls-cert-file=/certs/tls.crt \
    --tls-private-key-file=/certs/tls.key \
    --client-ca-file=/certs/tls.crt \
    --authorization-mode=AlwaysAllow \
    --token-auth-file=/certs/tokens.csv \
    --etcd-servers="http://${INNER_BRIDGE_IP}:${FORWARD_LOCAL_PORT}" \
    --service-cluster-ip-range=10.96.0.0/12 \
    --allow-privileged=true \
    --disable-admission-plugins=MutatingAdmissionWebhook,ValidatingAdmissionWebhook \
    --service-account-issuer=https://kubernetes.default.svc.cluster.local \
    --service-account-signing-key-file=/certs/sa.key \
    --service-account-key-file=/certs/sa.pub

### === Attendi readiness ===
echo ">> Attendo readiness https://127.0.0.1:${HOST_BIND_PORT}/healthz ..."
for i in {1..60}; do
  if curl -sk -H "Authorization: Bearer ${TOK}" "https://127.0.0.1:${HOST_BIND_PORT}/healthz" | grep -q '^ok$'; then
    echo ">> APIServer effimero READY."
    break
  fi
  sleep 1
  if [[ $i -eq 60 ]]; then
    echo "!! L'APIServer non è diventato ready. Log ultimi 200:" >&2
    docker logs "$NAME" --tail=200 >&2 || true
    exit 1
  fi
done

### === Esempio: crea un ClusterRoleBinding (cluster-admin agli anonimi — SOLO LAB!) ===
cat > /tmp/crb.json <<'JSON'
{
  "apiVersion": "rbac.authorization.k8s.io/v1",
  "kind": "ClusterRoleBinding",
  "metadata": {"name": "unauthenticated-admin"},
  "roleRef": {"apiGroup":"rbac.authorization.k8s.io","kind":"ClusterRole","name":"cluster-admin"},
  "subjects": [{"kind":"Group","apiGroup":"rbac.authorization.k8s.io","name":"system:unauthenticated"}]
}
JSON

echo ">> Creo il ClusterRoleBinding (PERICOLOSO in ambienti reali!)"
curl -sk -H "Authorization: Bearer ${TOK}" -H "Content-Type: application/json" \
  --data-binary @/tmp/crb.json \
  "https://127.0.0.1:${HOST_BIND_PORT}/apis/rbac.authorization.k8s.io/v1/clusterrolebindings" | jq .

### === Verifica CRB ===
echo ">> Verifica CRB creato:"
curl -sk -H "Authorization: Bearer ${TOK}" \
  "https://127.0.0.1:${HOST_BIND_PORT}/apis/rbac.authorization.k8s.io/v1/clusterrolebindings/unauthenticated-admin" \
  | jq -r '.metadata.name, .roleRef.name' | sed 's/^/  /'

echo
echo ">> FATTO."
echo "   - URL API: https://127.0.0.1:${HOST_BIND_PORT}"
echo "   - Token   : $(cat "${CERT_DIR}/token.txt")"
docker rm -f ${NAME}
