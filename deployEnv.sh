#!/bin/bash

# ========================
# ⚙️  Variabili configurabili
# ========================
CLUSTER_NAME="kind-cluster"
NUM_WORKERS=2
REGISTRY_NAME="registry"
REGISTRY_PORT=5000
REGISTRY_USER="testuser"
REGISTRY_PASS="testpassword"
REGISTRY_CN="registry.local"

HTPASSWD_PATH="./pb/auth/htpasswd"
CERT_CRT_PATH="./pb/certs/domain.crt"
CERT_KEY_PATH="./pb/certs/domain.key"

INSTALL_CILIUM=false

# ========================
# 🔧 Funzione per creare il cluster Kind
# ========================
create_kind_cluster() {
  echo "➡️  Creazione cluster Kind \"$CLUSTER_NAME\" con $NUM_WORKERS worker(s)..."

  # Costruzione dinamica dei nodi worker
  WORKER_NODES=""
  for ((i = 0; i < NUM_WORKERS; i++)); do
    WORKER_NODES+="  - role: worker"$'\n'
  done

  cat <<EOF | kind create cluster --name "$CLUSTER_NAME" --config=-
kind: Cluster
apiVersion: kind.x-k8s.io/v1alpha4
networking:
  disableDefaultCNI: true
  podSubnet: "10.244.0.0/16"
  serviceSubnet: "10.96.0.0/12"
containerdConfigPatches:
  - |
    [plugins."io.containerd.grpc.v1.cri".registry]
      [plugins."io.containerd.grpc.v1.cri".registry.mirrors."${REGISTRY_NAME}:${REGISTRY_PORT}"]
        endpoint = ["https://${REGISTRY_NAME}:${REGISTRY_PORT}"]
      [plugins."io.containerd.grpc.v1.cri".registry.configs."${REGISTRY_NAME}:${REGISTRY_PORT}".auth]
        username = "${REGISTRY_USER}"
        password = "${REGISTRY_PASS}"
      [plugins."io.containerd.grpc.v1.cri".registry.configs."${REGISTRY_NAME}:${REGISTRY_PORT}".tls]
        insecure_skip_verify = true
nodes:
  - role: control-plane
    extraPortMappings:
      - containerPort: ${REGISTRY_PORT}
    extraMounts:
      - hostPath: $(pwd)/pb/certs
        containerPath: /certs
$WORKER_NODES
EOF
}

# ========================
# 🧪 Verifica se il cluster esiste già
# ========================
if kind get clusters | grep -q "^$CLUSTER_NAME$"; then
  echo "✅ Cluster \"$CLUSTER_NAME\" già esistente, nessuna creazione necessaria."
else
  create_kind_cluster
fi

# ========================
# 📦 Helm repo check e add
# ========================
declare -A HELM_REPOS=(
  ["open-telemetry"]="https://open-telemetry.github.io/opentelemetry-helm-charts"
  ["jaegertracing"]="https://jaegertracing.github.io/helm-charts"
  ["prometheus-community"]="https://prometheus-community.github.io/helm-charts"
  ["grafana"]="https://grafana.github.io/helm-charts"
  ["opensearch"]="https://opensearch-project.github.io/helm-charts"
  ["cilium"]="https://helm.cilium.io/"
)

for repo in "${!HELM_REPOS[@]}"; do
  if helm repo list | grep -q "$repo"; then
    echo "✅ Helm repo \"$repo\" già presente."
  else
    echo "➡️  Aggiunta repo Helm: $repo"
    helm repo add "$repo" "${HELM_REPOS[$repo]}"
  fi
done

echo "➡️  Aggiornamento dei repo Helm..."
helm repo update

# ========================
# 📦 Install network CNI
# ========================
helm install cilium cilium/cilium --version 1.18.0 --namespace kube-system
#if [ "$INSTALL_CILIUM" = true ]; then
#  CILIUM_CLI_VERSION=$(curl -s https://raw.githubusercontent.com/cilium/cilium-cli/main/stable.txt)
#  CLI_ARCH=amd64
#  if [ "$(uname -m)" = "aarch64" ]; then CLI_ARCH=arm64; fi
#  curl -L --fail --remote-name-all https://github.com/cilium/cilium-cli/releases/download/${CILIUM_CLI_VERSION}/cilium-linux-${CLI_ARCH}.tar.gz{,.sha256sum}
#  sha256sum --check cilium-linux-${CLI_ARCH}.tar.gz.sha256sum
#  sudo tar xzvfC cilium-linux-${CLI_ARCH}.tar.gz /usr/local/bin
#  rm cilium-linux-${CLI_ARCH}.tar.gz{,.sha256sum}
#fi
#cilium install --version 1.18.0

# ========================
# 🔐 Creazione credenziali HTPASSWD
# ========================
if [ ! -f "$HTPASSWD_PATH" ]; then
  echo "➡️  Generazione file htpasswd..."
  mkdir -p "$(dirname "$HTPASSWD_PATH")"
  htpasswd -Bbn "$REGISTRY_USER" "$REGISTRY_PASS" > "$HTPASSWD_PATH"
else
  echo "✅ File htpasswd già esistente."
fi

# ========================
# 🔐 Generazione certificati TLS
# ========================
if [ ! -f "$CERT_CRT_PATH" ] || [ ! -f "$CERT_KEY_PATH" ]; then
  echo "➡️  Generazione certificati TLS self-signed..."
  mkdir -p "$(dirname "$CERT_CRT_PATH")"
  mkdir -p "$(dirname "$CERT_KEY_PATH")"
  openssl req -newkey rsa:4096 -nodes -sha256 \
    -keyout "$CERT_KEY_PATH" \
    -x509 -days 365 \
    -out "$CERT_CRT_PATH" \
    -subj "/CN=$REGISTRY_CN"
else
  echo "✅ Certificati TLS già esistenti."
fi

# ========================
# 🐳 Avvio del registry Docker
# ========================
if docker ps -a --format '{{.Names}}' | grep -q "^$REGISTRY_NAME$"; then
  echo "✅ Container registry già esistente."
else
  echo "➡️  Avvio del registry Docker..."
  docker run -d --restart=always --name $REGISTRY_NAME \
    -v "$(pwd)/pb/auth:/auth" \
    -v "$(pwd)/pb/certs:/certs" \
    -e REGISTRY_HTTP_ADDR=0.0.0.0:${REGISTRY_PORT} \
    -e "REGISTRY_AUTH=htpasswd" \
    -e "REGISTRY_AUTH_HTPASSWD_REALM=Registry Realm" \
    -e "REGISTRY_AUTH_HTPASSWD_PATH=/auth/htpasswd" \
    -e "REGISTRY_HTTP_TLS_CERTIFICATE=/certs/domain.crt" \
    -e "REGISTRY_HTTP_TLS_KEY=/certs/domain.key" \
    -p ${REGISTRY_PORT}:${REGISTRY_PORT} \
    registry:2
fi

# ========================
# 🔐 Login al registry
# ========================
echo "➡️  Login a localhost:${REGISTRY_PORT}..."
docker login localhost:${REGISTRY_PORT} -u "$REGISTRY_USER" -p "$REGISTRY_PASS"

# ========================
# 🔗 Connessione del registry alla rete Kind
# ========================
if docker network inspect kind &>/dev/null; then
  if docker network inspect kind | grep -q "\"Name\": \"$REGISTRY_NAME\""; then
    echo "✅ Registry già connesso alla rete kind."
  else
    echo "➡️  Connessione del registry alla rete Kind..."
    docker network connect kind "$REGISTRY_NAME" || true
  fi
else
  echo "⚠️  La rete Docker 'kind' non esiste. Assicurati che Kind sia installato correttamente."
fi

# ========================
# 🚀 Esecuzione Skaffold
# ========================
echo "➡️  Esecuzione skaffold run..."
skaffold run

# ========================
# 🚀 Avvio del frontend-proxy in localhost:8080 (e di ciò che collega - prometheus, etc...)
# ========================
kubectl --namespace dmz port-forward svc/frontend-proxy 8080:8080 --address=0.0.0.0