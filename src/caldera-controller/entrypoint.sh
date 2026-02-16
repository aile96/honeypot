#!/usr/bin/env sh
set -e

log()  { printf "\033[1;36m[INFO]\033[0m %s\n" "$*"; }
warn() { printf "\033[1;33m[WARN]\033[0m %s\n" "$*"; }
err()  { printf "\033[1;31m[ERR ]\033[0m %s\n" "$*" >&2; }

# --- Config (override with env vars if you like) ---
: "${KUBECONFIG:=/kube/kubeconfig}"          # default mount path
: "${K8S_WAIT_TIMEOUT:=3000}"                 # overall wait budget in seconds
: "${K8S_NS_WAIT_TIMEOUT:=1800}"              # per-namespace rollout timeout
: "${K8S_STRICT:=false}"                     # if "true", fail when workloads aren't all ready

# If ~/.kube/config exists and no KUBECONFIG env was provided, use it.
if [ -z "${KUBECONFIG_SET:-}" ] && [ -f "${HOME}/.kube/config" ] && [ ! -f "$KUBECONFIG" ]; then
  export KUBECONFIG="${HOME}/.kube/config"
fi
export KUBECONFIG

log "== auto-starter =="
log "CALDERA_URL=${CALDERA_URL:-http://localhost:8888}"
log "GROUP=${GROUP:-cluster}"
log "ADV_LIST=${ADV_LIST:-KC1 – Safe Mining Emulation}"
if [ -n "$CALDERA_KEY" ]; then log "CALDERA_KEY set (hidden)"; else warn "CALDERA_KEY not set"; fi
log "KUBECONFIG=${KUBECONFIG}"
log "==================="

# --- 1) Wait for kubeconfig file to appear ---
start_ts=$(date +%s)
if [ ! -s "$KUBECONFIG" ]; then
  warn "Kubeconfig not found yet at $KUBECONFIG; sleeping..."
fi
until [ -s "$KUBECONFIG" ]; do
  now=$(date +%s)
  if [ $(( now - start_ts )) -ge "$K8S_WAIT_TIMEOUT" ]; then
    err "Timed out waiting for kubeconfig at $KUBECONFIG"
    exit 1
  fi
  sleep 2
done
log "Kubeconfig present."

# --- 2) Wait for API server readiness ---
log "Waiting for Kubernetes API to be ready..."
# Try /readyz first; fall back to a simple 'kubectl get ns'
until kubectl --request-timeout=5s get --raw='/readyz' >/dev/null 2>&1 || \
      kubectl --request-timeout=5s get ns >/dev/null 2>&1; do
  now=$(date +%s)
  if [ $(( now - start_ts )) -ge "$K8S_WAIT_TIMEOUT" ]; then
    err "Timed out waiting for API server readiness."
    exit 1
  fi
  sleep 5
done
log "API server is reachable."

# --- 3) Wait for all nodes to be Ready (best effort if single-node kind/k3d) ---
if ! kubectl get nodes >/dev/null 2>&1; then
  warn "Cannot list nodes; continuing."
else
  log "Waiting for nodes to be Ready..."
  if ! kubectl wait --for=condition=Ready nodes --all --timeout="${K8S_WAIT_TIMEOUT}s"; then
    if [ "$K8S_STRICT" = "true" ]; then
      err "Nodes did not all become Ready in time."
      exit 1
    else
      warn "Some nodes not Ready; continuing (K8S_STRICT=false)."
    fi
  fi
fi

# --- 4) Wait for workloads to be up across namespaces (best effort) ---
log "Waiting for workloads (Deployments/DaemonSets/StatefulSets) to be ready..."
NAMESPACES=$(kubectl get ns -o jsonpath='{.items[*].metadata.name}' 2>/dev/null || echo "")
for ns in $NAMESPACES; do
  # Deployments
  if kubectl -n "$ns" get deploy >/dev/null 2>&1; then
    kubectl -n "$ns" rollout status deploy --all --timeout="${K8S_NS_WAIT_TIMEOUT}s" || \
      { [ "$K8S_STRICT" = "true" ] && err "Deployments not ready in ns/$ns" && exit 1 || warn "Deployments not ready in ns/$ns"; }
  fi
  # DaemonSets
  if kubectl -n "$ns" get ds >/dev/null 2>&1; then
    kubectl -n "$ns" rollout status ds --all --timeout="${K8S_NS_WAIT_TIMEOUT}s" || \
      { [ "$K8S_STRICT" = "true" ] && err "DaemonSets not ready in ns/$ns" && exit 1 || warn "DaemonSets not ready in ns/$ns"; }
  fi
  # StatefulSets
  if kubectl -n "$ns" get sts >/dev/null 2>&1; then
    kubectl -n "$ns" rollout status sts --all --timeout="${K8S_NS_WAIT_TIMEOUT}s" || \
      { [ "$K8S_STRICT" = "true" ] && err "StatefulSets not ready in ns/$ns" && exit 1 || warn "StatefulSets not ready in ns/$ns"; }
  fi
done
log "Cluster readiness checks completed."

# --- 5) Start the app ---
app_start_ts="$(date '+%Y-%m-%d %H:%M:%S %Z')"
log "Starting auto_starter.py at ${app_start_ts}"

if python /app/auto_starter.py; then
  app_exit_code=0
else
  app_exit_code=$?
fi

app_end_ts="$(date '+%Y-%m-%d %H:%M:%S %Z')"
if [ "$app_exit_code" -eq 0 ]; then
  log "Finished auto_starter.py at ${app_end_ts}"
else
  err "auto_starter.py exited with code ${app_exit_code} at ${app_end_ts}"
fi

exit "$app_exit_code"
