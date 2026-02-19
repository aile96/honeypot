#!/usr/bin/env bash
set -euo pipefail

SCRIPTS_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMMON_LIB="${SCRIPTS_ROOT}/lib/common.sh"
if [[ ! -f "${COMMON_LIB}" ]]; then
  printf "[ERROR] Common library not found: %s\n" "${COMMON_LIB}" >&2
  return 1 2>/dev/null || exit 1
fi
source "${COMMON_LIB}"

require_bin() {
  command -v "$1" >/dev/null 2>&1 || die "Required binary '$1' not found in PATH"
}

# ==========================
# Variables
# ==========================
SA_NAMESPACE="kube-system"
SA_NAME="controller-admin"
CRB_NAME="controller-admin"
OUT_DIR="pb/docker/controller/kube"
OUT_FILE="${OUT_DIR}/kubeconfig"
CALDERA_CONTROLLER="${CALDERA_CONTROLLER:-controller}"
ATTACKER="${ATTACKER:-attacker}"
MISSING_POLICY="${MISSING_POLICY:-false}"
RECURSIVE_DNS="${RECURSIVE_DNS:-true}"
: "${KUBE_CONTEXT:=$(kubectl config current-context 2>/dev/null || true)}"

[[ -n "${KUBE_CONTEXT}" ]] || die "Unable to resolve kubectl context."
normalize_bool_var MISSING_POLICY
normalize_bool_var RECURSIVE_DNS

# ==========================
# Helpers
# ==========================
kubectl_get() {
  if [[ -n "${KUBE_CONTEXT:-}" ]]; then
    kubectl --context "$KUBE_CONTEXT" "$@"
  else
    kubectl "$@"
  fi
}

node_internal_ip() {
  local node="$1"
  kubectl_get get node "$node" \
    -o jsonpath='{.status.addresses[?(@.type=="InternalIP")].address}'
}

find_container_by_ip() {
  find_docker_container_by_ip "$1"
}

# Ensure required tools for core operations
require_bin kubectl
require_bin docker

# --------------------------
# 1) Ensure ServiceAccount and ClusterRoleBinding
# --------------------------
mkdir -p "${OUT_DIR}"
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
CURRENT_CONTEXT="${KUBE_CONTEXT}"
[[ -n "$CURRENT_CONTEXT" ]] || die "Unable to resolve current kubectl context."
CLUSTER_NAME="$(kubectl config view --raw --minify --context "${CURRENT_CONTEXT}" -o jsonpath='{.contexts[0].context.cluster}')"
CA_DATA="$(kubectl config view --raw --minify --context "${CURRENT_CONTEXT}" -o jsonpath='{.clusters[0].cluster.certificate-authority-data}')"
CURRENT_SERVER="$(kubectl config view --raw --minify --context "${CURRENT_CONTEXT}" -o jsonpath='{.clusters[0].cluster.server}')"

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
TLS_CONFIG_LINE="insecure-skip-tls-verify: true"
if [[ -n "$CA_DATA" ]]; then
  TLS_CONFIG_LINE="certificate-authority-data: ${CA_DATA}"
else
  warn "Cluster CA data not found. Falling back to insecure-skip-tls-verify."
fi

cat > "${OUT_FILE}" <<EOF
apiVersion: v1
kind: Config
clusters:
- cluster:
    server: ${SERVER_URL}
    ${TLS_CONFIG_LINE}
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

if docker ps -a --format '{{.Names}}' | grep -qx "${CALDERA_CONTROLLER}"; then
  docker cp ./pb/docker/controller/kube "${CALDERA_CONTROLLER}:/kube" || die "Failed to copy kubeconfig into '${CALDERA_CONTROLLER}'."
else
  warn "Container '${CALDERA_CONTROLLER}' not found. Skipping kubeconfig copy."
fi
log "Kubeconfig ready: ${OUT_FILE}"

# --------------------------
# 6) Setting Policies in namespaces to avoid hostpath, privileged and privilege escalation
# --------------------------
if ! is_true "${MISSING_POLICY}"; then
  for ns_var in APP_NAMESPACE DAT_NAMESPACE DMZ_NAMESPACE MEM_NAMESPACE PAY_NAMESPACE TST_NAMESPACE; do
    ns="${!ns_var:-}"
    if [[ -n "$ns" ]]; then
      log "Labeling namespace: $ns"
      kubectl_get label namespace "$ns" \
        pod-security.kubernetes.io/enforce=restricted \
        pod-security.kubernetes.io/warn=restricted \
        pod-security.kubernetes.io/audit=restricted \
        --overwrite
    else
      log "Skipping: variable $ns_var is not set or empty"
    fi
  done
else
  log "MISSING_POLICY is 'true' → skipping Pod Security label application."
fi

# --------------------------
# 7) Setting CoreDNS to non-recursive mode OR inserting attacker zone
# --------------------------
if ! is_true "${RECURSIVE_DNS}"; then
  log "Checking CoreDNS ConfigMap for 'forward'..."
  if kubectl_get -n kube-system get configmap coredns -o yaml | grep -q '^[[:space:]]*forward[[:space:]]'; then
    log "Disabling recursion: removing 'forward' (handles block and single-line forms)..."
    kubectl_get -n kube-system get configmap coredns -o yaml \
      | awk '
          # Start skipping when we hit: forward ... {
          /^[[:space:]]*forward[[:space:]].*{/ { blk=1; depth=1; next }
          # While skipping, track nested braces and skip lines
          blk {
            add=gsub(/{/,"{"); subc=gsub(/}/,"}")
            depth += add - subc
            if (depth <= 0) blk=0
            next
          }
          # Skip single-line forward directives (no opening brace)
          /^[[:space:]]*forward[[:space:]].*$/ { next }
          # Otherwise print the line
          { print }
        ' \
      | kubectl_get -n kube-system apply -f -
    log "Restarting the CoreDNS deployment..."
    kubectl_get -n kube-system rollout restart deployment coredns
    log "Done! CoreDNS is now non-recursive."
  else
    log "No 'forward' directive found — nothing to change."
  fi
else
  ZONE="${ATTACKER}"
  CONTAINER_NAME="${ATTACKER}"

  if ! docker ps -a --format '{{.Names}}' | grep -qx "${CONTAINER_NAME}"; then
    warn "Container '${CONTAINER_NAME}' not found. Skipping attacker DNS zone insertion."
    return 0 2>/dev/null || exit 0
  fi

  # get docker IP (first network IP)
  DOCKER_IP=$(docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' "${CONTAINER_NAME}" 2>/dev/null || true)
  if [[ -z "${DOCKER_IP}" ]]; then
    err "Could not determine IP for docker container '${CONTAINER_NAME}'. Aborting."
    return 1 2>/dev/null || exit 1
  fi

  # Build the zone block (no leading indentation)
  read -r -d '' ZONE_BLOCK <<EOF || true
${ZONE}:53 {
    errors
    cache 30
    forward . ${DOCKER_IP}
    reload
}
EOF

  # Fetch current Corefile via JSON to avoid YAML block marker problems
  CURRENT_COREFILE="$(kubectl_get -n kube-system get cm coredns -o jsonpath='{.data.Corefile}' 2>/dev/null || true)"
  if [[ -z "${CURRENT_COREFILE}" ]]; then
    err "Failed to read kube-system/coredns Corefile. Aborting."
    return 1 2>/dev/null || exit 1
  fi

  # If the zone block already exists (top-level "zone" optionally with :port), exit
  if printf '%s\n' "${CURRENT_COREFILE}" | grep -qE "^[[:space:]]*${ZONE}(:[0-9]+)?[[:space:]]*\\{" ; then
    log "Zone block for '${ZONE}' already present in Corefile. Nothing to do."
    return 0 2>/dev/null || exit 0
  fi

  # Remove any pre-existing occurrences of the same zone block (balanced-brace removal),
  # then insert the zone block before the main .:53 block (if present) or prepend.
  TMPDIR="$(mktemp -d)"
  trap_add '[[ -n "${TMPDIR:-}" ]] && rm -rf "${TMPDIR}"' EXIT
  printf '%s\n' "${CURRENT_COREFILE}" > "${TMPDIR}/corefile.orig"

  # remove pre-existing zone blocks for the same zone (balanced braces)
  awk -v zone="$ZONE" '
    BEGIN { skip=0; depth=0 }
    {
      if (skip) {
        add = gsub(/\{/, "{")
        subc = gsub(/\}/, "}")
        depth += add - subc
        if (depth <= 0) skip=0
        next
      }
      line=$0
      sub(/^[[:space:]]+/, "", line)
      regex = "^" zone "(:[0-9]+)?[[:space:]]*\\{"
      if (line ~ regex) { skip=1; depth=1; next }
      print $0
    }
  ' "${TMPDIR}/corefile.orig" > "${TMPDIR}/corefile.cleaned"

  # Insert the zone block before the .:53 block if present, otherwise prepend
  if grep -qE '^[[:space:]]*\.?:53[[:space:]]*\{' "${TMPDIR}/corefile.cleaned"; then
    awk -v block="${ZONE_BLOCK}" '
      BEGIN { inserted=0 }
      {
        if (!inserted && $0 ~ /^[[:space:]]*\.?:53[[:space:]]*\{/) {
          printf "%s\n\n", block
          inserted=1
        }
        print $0
      }
      END {
        if (!inserted) printf "%s\n", block
      }
    ' "${TMPDIR}/corefile.cleaned" > "${TMPDIR}/corefile.new"
  else
    printf "%s\n\n%s\n" "${ZONE_BLOCK}" "$(cat "${TMPDIR}/corefile.cleaned")" > "${TMPDIR}/corefile.new"
  fi

  # Read the NEW corefile content into a variable (preserve newlines)
  NEW_COREFILE=$(cat "${TMPDIR}/corefile.new")

  # Use jq to patch the ConfigMap JSON safely (this avoids depending on YAML block markers).
  # Note: jq must be available. If jq is missing, print an error and abort.
  if ! command -v jq >/dev/null 2>&1; then
    [[ -n "${TMPDIR:-}" ]] && rm -rf "${TMPDIR}" && TMPDIR=""
    err "jq is required to safely patch the ConfigMap but was not found. Install jq and retry."
    return 1 2>/dev/null || exit 1
  fi

  kubectl_get -n kube-system get cm coredns -o json \
    | jq --arg cf "$NEW_COREFILE" '.data.Corefile = $cf' \
    | kubectl_get -n kube-system apply -f - >/dev/null

  log "Inserted zone block for '${ZONE}' forwarding to ${DOCKER_IP} into kube-system/coredns ConfigMap."
  log "Restarting the CoreDNS deployment..."
  kubectl_get -n kube-system rollout restart deployment coredns
  log "Done."
  [[ -n "${TMPDIR:-}" ]] && rm -rf "${TMPDIR}" && TMPDIR=""
fi
