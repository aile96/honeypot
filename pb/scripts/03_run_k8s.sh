#!/usr/bin/env bash
set -euo pipefail

# ==========================
# Utility functions
# ==========================
log()  { printf "\033[1;36m[INFO]\033[0m %s\n" "$*"; }
warn() { printf "\033[1;33m[WARN]\033[0m %s\n" "$*"; }
err()  { printf "\033[1;31m[ERR ]\033[0m %s\n" "$*" >&2; }
die()  { echo -e "[ERROR] $*" >&2; exit 1; }

require_bin() {
  command -v "$1" >/dev/null 2>&1 || die "Required binary '$1' not found in PATH"
}

# ==========================
# Variables
# ==========================
SA_NAMESPACE="kube-system"
SA_NAME="controller-admin"
CRB_NAME="controller-admin"
OUT_DIR="pb/docker/controller"
OUT_FILE="${OUT_DIR}/kubeconfig"

# ==========================
# Optional .env loader
# ==========================
if [[ -n "${ENV_FILE:-}" && -f "$ENV_FILE" ]]; then
  log "Loading vars from $ENV_FILE..."
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
else
  [[ -n "${ENV_FILE:-}" ]] && err "No file $ENV_FILE found"
fi

# ==========================
# Helpers
# ==========================
kubectl_get() {
  kubectl "$@"
}

node_internal_ip() {
  local node="$1"
  kubectl_get get node "$node" \
    -o jsonpath='{.status.addresses[?(@.type=="InternalIP")].address}'
}

find_container_by_ip() {
  local ip="$1"
  local cid name ips nets
  while IFS= read -r cid; do
    name="$(docker inspect -f '{{.Name}}' "$cid" 2>/dev/null | sed 's#^/##' || true)"
    ips="$(docker inspect -f '{{range $k,$v := .NetworkSettings.Networks}}{{$v.IPAddress}} {{end}}' "$cid" 2>/dev/null | xargs || true)"
    nets="$(docker inspect -f '{{range $k,$v := .NetworkSettings.Networks}}{{$k}} {{end}}' "$cid" 2>/dev/null | xargs || true)"
    for tip in $ips; do
      if [[ "$tip" == "$ip" ]]; then
        printf '%s;%s' "$name" "$nets"
        return 0
      fi
    done
  done < <(docker ps -q)
  return 1
}

ctx_exists() {
  kubectl config get-contexts -o name | grep -qx "$1"
}

jsonpath_val() {
  # $1 = context name, $2 = field (cluster|user|namespace)
  kubectl config view -o jsonpath="{.contexts[?(@.name==\"$1\")].context.$2}"
}

# Ensure required tools for core operations
require_bin kubectl
command -v docker >/dev/null 2>&1 || warn "Docker not found — some Docker-backed cluster detection may fail"

# --------------------------
# 0) Build artifacts via Skaffold
# --------------------------

# Discover current context and its details (fail fast if missing)
CTX="$(kubectl config current-context || true)"
[[ -n "${CTX:-}" ]] || die "kubectl has no current-context set."

CLUSTER="$(jsonpath_val "$CTX" cluster || true)"
USER="$(jsonpath_val "$CTX" user || true)"
NS="$(jsonpath_val "$CTX" namespace || true)"
[[ -n "${CLUSTER:-}" && -n "${USER:-}" ]] || die "Unable to resolve cluster/user for context '$CTX'."

NS_EFFECTIVE="${NS:-default}"
log "Current context: $CTX | cluster=$CLUSTER | user=$USER | namespace=$NS_EFFECTIVE"

TARGET_CTX="honeypot"

# Create or update the honeypot context only if needed
if ctx_exists "$TARGET_CTX"; then
  EXIST_CLUSTER="$(jsonpath_val "$TARGET_CTX" cluster || true)"
  EXIST_USER="$(jsonpath_val "$TARGET_CTX" user || true)"
  EXIST_NS="$(jsonpath_val "$TARGET_CTX" namespace || true)"
  EXIST_NS_EFFECTIVE="${EXIST_NS:-default}"

  if [[ "$EXIST_CLUSTER" == "$CLUSTER" && "$EXIST_USER" == "$USER" && "$EXIST_NS_EFFECTIVE" == "$NS_EFFECTIVE" ]]; then
    log "Context '$TARGET_CTX' already matches desired settings — skipping set-context."
  else
    log "Updating context '$TARGET_CTX' to cluster=$CLUSTER user=$USER namespace=$NS_EFFECTIVE ..."
    kubectl config set-context "$TARGET_CTX" \
      --cluster="$CLUSTER" --user="$USER" --namespace="$NS_EFFECTIVE" >/dev/null
  fi
else
  log "Creating context '$TARGET_CTX' (cluster=$CLUSTER user=$USER namespace=$NS_EFFECTIVE) ..."
  kubectl config set-context "$TARGET_CTX" \
    --cluster="$CLUSTER" --user="$USER" --namespace="$NS_EFFECTIVE" >/dev/null
fi

# Switch to honeypot only if not current
CURRENT_CTX="$(kubectl config current-context || true)"
if [[ "$CURRENT_CTX" != "$TARGET_CTX" ]]; then
  log "Switching kubectl context to '$TARGET_CTX' ..."
  kubectl config use-context "$TARGET_CTX" >/dev/null
else
  log "Already using context '$TARGET_CTX' — skipping switch."
fi

# Skaffold: set local-cluster=false only if needed (and if installed)
if command -v skaffold >/dev/null 2>&1; then
  CURRENT_VAL="$(skaffold config list --kube-context "$TARGET_CTX" 2>/dev/null | awk '$1=="local-cluster"{print $2; exit}')"
  if [[ "${CURRENT_VAL:-}" == "false" ]]; then
    log "skaffold: 'local-cluster' already false for '$TARGET_CTX' — skipping."
  else
    log "skaffold: setting local-cluster=false for '$TARGET_CTX' ..."
    skaffold config set --kube-context "$TARGET_CTX" local-cluster false >/dev/null
  fi

  log "Running skaffold deployment and building..."
  skaffold run --tag "${IMAGE_VERSION:-2.0.2}" \
  --default-repo="${REGISTRY_NAME:-registry}:${REGISTRY_PORT:-5000}" \
  --verbosity error "$@"
else
  warn "Skaffold not found — skipping skaffold config and run."
fi

mkdir -p "${OUT_DIR}"

# --------------------------
# 1) Ensure ServiceAccount and ClusterRoleBinding
# --------------------------
log "Ensuring ServiceAccount and ClusterRoleBinding exist..."
kubectl_get get sa "${SA_NAME}" -n "${SA_NAMESPACE}" >/dev/null 2>&1 || \
  kubectl_get create sa "${SA_NAME}" -n "${SA_NAMESPACE}"

kubectl_get get clusterrolebinding "${CRB_NAME}" >/dev/null 2>&1 || \
  kubectl_get create clusterrolebinding "${CRB_NAME}" \
    --clusterrole=cluster-admin \
    --serviceaccount="${SA_NAMESPACE}:${SA_NAME}"

# --------------------------
# 2) Extract cluster info and CA data
# --------------------------
CURRENT_CONTEXT="$(kubectl config current-context || true)"
CLUSTER_NAME="$(kubectl config view -o jsonpath='{.contexts[?(@.name=="'${CURRENT_CONTEXT}'")].context.cluster}')"
CA_DATA="$(kubectl config view --raw -o jsonpath='{.clusters[?(@.name=="'"${CLUSTER_NAME}"'")].cluster.certificate-authority-data}')"
CURRENT_SERVER="$(kubectl config view --raw -o jsonpath='{.clusters[?(@.name=="'"${CLUSTER_NAME}"'")].cluster.server}')"

[[ -n "$CLUSTER_NAME" ]] || die "Unable to resolve cluster name from current context."
[[ -n "$CURRENT_SERVER" ]] || die "Unable to resolve current cluster server endpoint."

# --------------------------
# 3) Compute API server endpoint reachable from Docker
# --------------------------
SERVER_HOST=""
DOCKER_NET=""

# 3a) Known cases (kind/k3d/minikube)
if [[ "${CURRENT_CONTEXT}" == kind-* ]]; then
  if docker ps --format '{{.Names}}' | grep -q '^kind-control-plane$'; then
    SERVER_HOST="kind-control-plane"
  else
    KNAME="${CURRENT_CONTEXT#kind-}"
    if docker ps --format '{{.Names}}' | grep -q "^${KNAME}-control-plane$"; then
      SERVER_HOST="${KNAME}-control-plane"
    fi
  fi
  DOCKER_NET="kind"
elif [[ "${CURRENT_CONTEXT}" == k3d-* ]]; then
  KNAME="${CURRENT_CONTEXT#k3d-}"
  SERVER_HOST="k3d-${KNAME}-server-0"
  DOCKER_NET="k3d-${KNAME}"
elif [[ "${CURRENT_CONTEXT}" == "minikube" ]]; then
  if docker ps --format '{{.Names}}' | grep -qx 'minikube'; then
    SERVER_HOST="minikube"
    DOCKER_NET="minikube"
  fi
fi

# 3b) Fallback: map control-plane InternalIP -> Docker container (name + networks)
if [[ -z "$SERVER_HOST" || -z "$DOCKER_NET" ]]; then
  CP_NODE="$(kubectl_get get nodes -l 'node-role.kubernetes.io/control-plane' -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}' || true)"
  [[ -z "${CP_NODE// }" ]] && CP_NODE="$(kubectl_get get nodes -l 'node-role.kubernetes.io/master' -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}' || true)"
  CP_NODE="$(echo "$CP_NODE" | head -n1 | xargs || true)"

  if [[ -n "$CP_NODE" ]]; then
    CP_IP="$(node_internal_ip "$CP_NODE")"
    if [[ -n "$CP_IP" ]]; then
      FOUND="$(find_container_by_ip "$CP_IP" || true)"
      if [[ -n "$FOUND" ]]; then
        SERVER_HOST="$(echo "$FOUND" | cut -d';' -f1)"
        DOCKER_NET="$(echo "$FOUND" | cut -d';' -f2- | awk '{print $1}')"
      fi
    fi
  fi
fi

# Build the final server URL
SCHEMA="${CURRENT_SERVER%%://*}"
if [[ -n "$SERVER_HOST" ]]; then
  PORT="$(kubectl_get -n default get endpoints kubernetes -o jsonpath='{.subsets[0].ports[0].port}' 2>/dev/null || echo 6443)"
  [[ -z "$PORT" ]] && PORT=6443
  SERVER_URL="${SCHEMA}://${SERVER_HOST}:${PORT}"
else
  SERVER_URL="${CURRENT_SERVER}"
fi

log "Using API server endpoint: ${SERVER_URL}"
[[ -n "${DOCKER_NET}" ]] && log "Docker network to use: ${DOCKER_NET}"

# --------------------------
# 4) Get ServiceAccount token (with legacy fallback)
# --------------------------
TOKEN="$(kubectl_get -n "${SA_NAMESPACE}" create token "${SA_NAME}" 2>/dev/null || true)"
if [[ -z "${TOKEN}" ]]; then
  warn "kubectl create token failed; trying legacy secret-based token retrieval..."
  SA_SECRET="$(kubectl_get -n "${SA_NAMESPACE}" get sa "${SA_NAME}" -o jsonpath='{.secrets[0].name}' 2>/dev/null || true)"
  [[ -z "$SA_SECRET" ]] && die "Failed to get token for ServiceAccount (no secret found)."
  TOKEN="$(kubectl_get -n "${SA_NAMESPACE}" get secret "${SA_SECRET}" -o go-template='{{.data.token | base64decode}}' 2>/dev/null || true)"
fi
[[ -z "${TOKEN}" ]] && die "Failed to obtain a token for ServiceAccount '${SA_NAMESPACE}/${SA_NAME}'."

# --------------------------
# 5) Write minimal kubeconfig file
# --------------------------
mkdir -p "${OUT_DIR}"
cat > "${OUT_FILE}" <<EOF
apiVersion: v1
kind: Config
clusters:
- cluster:
    server: ${SERVER_URL}
    insecure-skip-tls-verify: true
  name: ${CLUSTER_NAME}
contexts:
- context:
    cluster: ${CLUSTER_NAME}
    user: ${SA_NAME}
  name: ${CLUSTER_NAME}
current-context: ${CLUSTER_NAME}
users:
- name: ${SA_NAME}
  user:
    token: ${TOKEN}
EOF

chmod 600 "${OUT_FILE}"
log "Kubeconfig ready: ${OUT_FILE}"
