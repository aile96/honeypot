#!/usr/bin/env bash
set -euo pipefail

### === Parameters ===
APISERVER_IMAGE="${APISERVER_IMAGE:-registry.k8s.io/kube-apiserver:v1.30.0}"
HOST_BIND_PORT="${HOST_BIND_PORT:-16443}"       # HTTPS port exposed from ephemeral APIServer on host
NAME="${NAME:-k8s-apiserver-ephem}"
CERT_DIR="${CERT_DIR:-$(pwd)/apiserver-certs}"
FORWARD_LOCAL_PORT="${FORWARD_LOCAL_PORT:-2379}"   # local port in host where socat listen
FORWARD_TARGET_HOST="${FORWARD_TARGET_HOST:-kind-cluster-control-plane}"
FORWARD_TARGET_PORT="${FORWARD_TARGET_PORT:-12379}" # HTTP port of real etcd

### === Helper: installing minimal dependencies (socat, curl, jq, xxd) ===
install_if_missing() {
  local bin="$1" apk_pkg="$2" apt_pkg="$3"
  if ! command -v "$bin" >/dev/null 2>&1; then
    if command -v apk >/dev/null 2>&1; then
      apk add --no-cache "$apk_pkg"
    elif command -v apt-get >/dev/null 2>&1; then
      apt-get update -y
      DEBIAN_FRONTEND=noninteractive apt-get install -y "$apt_pkg"
    else
      echo ">> ERROR: missing $bin. No manager to install" >&2
      exit 1
    fi
  fi
}
install_if_missing socat socat socat
install_if_missing curl  curl  curl
install_if_missing jq    jq    jq
install_if_missing xxd   xxd   xxd

### === Running forward TCP: orchestrator :2379 -> control-plane:12379 ===
if ! ss -lnt | awk '{print $4}' | grep -q ":${FORWARD_LOCAL_PORT}$"; then
  echo ">> Running socat: :${FORWARD_LOCAL_PORT} -> ${FORWARD_TARGET_HOST}:${FORWARD_TARGET_PORT}"
  socat "TCP-LISTEN:${FORWARD_LOCAL_PORT},fork,reuseaddr" "TCP:${FORWARD_TARGET_HOST}:${FORWARD_TARGET_PORT}" &
  SOCAT_PID=$!
  trap '[[ -n "${SOCAT_PID:-}" ]] && kill ${SOCAT_PID} >/dev/null 2>&1 || true' EXIT
else
  echo ">> Socat already listening on: ${FORWARD_LOCAL_PORT}"
fi

# Quick test of forward from host
echo ">> Etcd forward test..."
for i in {1..10}; do
  if curl -sf "http://127.0.0.1:${FORWARD_LOCAL_PORT}/health" >/dev/null; then
    break
  fi
  sleep 1
  [[ $i -eq 10 ]] && { echo "!! etcd doesn't answer via forward"; exit 1; }
done
curl -s "http://127.0.0.1:${FORWARD_LOCAL_PORT}/health" || true

### === Find gateway IP of Docker daemon (from his container POV) ===
echo ">> Deriving IP gateway from containers POV..."
docker pull --quiet busybox:latest >/dev/null
INNER_BRIDGE_IP="$(docker run --rm busybox sh -c "ip route | awk '/default/ {print \$3}'")"
echo ">> INNER_BRIDGE_IP: ${INNER_BRIDGE_IP}"

# Verification from container POV
echo ">> Verification that containers see the forward:"
docker run --rm busybox sh -c "wget -qO- http://${INNER_BRIDGE_IP}:${FORWARD_LOCAL_PORT}/health" || {
  echo "!! The containers don't see ${INNER_BRIDGE_IP}:${FORWARD_LOCAL_PORT}/health" >&2
  exit 1
}

### === Cert and keys disposable ===
rm -rf "$CERT_DIR"; mkdir -p "$CERT_DIR"
docker run --rm -v "$CERT_DIR":/out alpine:3.20 sh -c '
  set -e
  apk add --no-cache openssl >/dev/null
  # serving cert per HTTPS
  openssl req -x509 -newkey rsa:2048 -nodes -sha256 -days 1 \
    -subj "/CN=localhost" \
    -keyout /out/tls.key -out /out/tls.crt >/dev/null
  # Keys for ServiceAccount JWT (private + public)
  openssl genrsa -out /out/sa.key 2048 >/dev/null 2>&1
  openssl rsa -in /out/sa.key -pubout -out /out/sa.pub >/dev/null 2>&1
'

### === Static token (admin in system:masters) ===
TOK="$(head -c16 /dev/urandom | xxd -p)"
echo "${TOK},admin,uid-admin,system:masters" > "${CERT_DIR}/tokens.csv"
echo ">> Bearer token (saved also in ${CERT_DIR}/token.txt): ${TOK}" | tee "${CERT_DIR}/token.txt" >/dev/null

### === Running ephemeral kube-apiserver ===
echo ">> Running ephemeral kube-apiserver..."
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

### === Waiting for readiness ===
echo ">> Waiting for readiness: https://127.0.0.1:${HOST_BIND_PORT}/healthz ..."
for i in {1..60}; do
  if curl -sk -H "Authorization: Bearer ${TOK}" "https://127.0.0.1:${HOST_BIND_PORT}/healthz" | grep -q '^ok$'; then
    echo ">> Ephemeral APIServer READY."
    break
  fi
  sleep 1
  if [[ $i -eq 60 ]]; then
    echo "!! APIServer didn't become ready. Last 200 logs:" >&2
    docker logs "$NAME" --tail=200 >&2 || true
    exit 1
  fi
done

### === Creating ClusterRoleBinding ===
cat > /tmp/crb.json <<'JSON'
{
  "apiVersion": "rbac.authorization.k8s.io/v1",
  "kind": "ClusterRoleBinding",
  "metadata": {"name": "unauthenticated-admin"},
  "roleRef": {"apiGroup":"rbac.authorization.k8s.io","kind":"ClusterRole","name":"cluster-admin"},
  "subjects": [{"kind":"Group","apiGroup":"rbac.authorization.k8s.io","name":"system:unauthenticated"}]
}
JSON

echo ">> Creating the ClusterRoleBinding"
curl -sk -H "Authorization: Bearer ${TOK}" -H "Content-Type: application/json" \
  --data-binary @/tmp/crb.json \
  "https://127.0.0.1:${HOST_BIND_PORT}/apis/rbac.authorization.k8s.io/v1/clusterrolebindings" | jq .

### === Verifying the CRB ===
echo ">> Verifying creation CRB:"
curl -sk -H "Authorization: Bearer ${TOK}" \
  "https://127.0.0.1:${HOST_BIND_PORT}/apis/rbac.authorization.k8s.io/v1/clusterrolebindings/unauthenticated-admin" \
  | jq -r '.metadata.name, .roleRef.name' | sed 's/^/  /'

echo
echo ">> DONE"
echo "   - Token   : $(cat "${CERT_DIR}/token.txt")"
echo "Removing ephemeral API server"
docker rm -f ${NAME}
echo "DONE"
