#!/usr/bin/env bash
set -euo pipefail

SCRIPTS_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPTS_ROOT}/../.." && pwd)"
COMMON_LIB="${SCRIPTS_ROOT}/lib/common.sh"
if [[ ! -f "${COMMON_LIB}" ]]; then
  printf "[ERROR] Common library not found: %s\n" "${COMMON_LIB}" >&2
  return 1 2>/dev/null || exit 1
fi
source "${COMMON_LIB}"

kubectl_ctx() {
  kubectl --context "$KUBE_CONTEXT" "$@"
}

# Get node IPv4 InternalIP only
node_internal_ip() {
  local node="$1"
  # list all InternalIP addresses and pick the first one that is NOT IPv6
  kubectl_ctx get node "$node" \
    -o jsonpath='{range .status.addresses[?(@.type=="InternalIP")]}{.address}{"\n"}{end}' \
    | grep -v ':' | head -n1
}

# Map an IP address to a docker container name and networks (IPv4 is enough)
find_container_by_ip() {
  find_docker_container_by_ip "$1"
}

# --------------
# Config / env
# --------------
: "${KUBE_CONTEXT:=$(kubectl config current-context 2>/dev/null || true)}"
: "${OUT_DIR:=./pb/docker/attacker/apiserver}"
: "${ATTACKER_DIR:=./pb/docker/attacker}"
: "${ETCD_DOCKER_IMAGE:=quay.io/coreos/etcd:v3.5.10}"
: "${PLAIN_PORT:=12379}"
: "${API_CERT:=false}"
: "${OPEN_PORTS:=false}"
: "${ETCD_EXPOSURE:=false}"
: "${ANONYMOUS_AUTH:=false}"
: "${REGISTRY_NAME:=registry}"
: "${REGISTRY_PORT:=5000}"
: "${REGISTRY_USER:?REGISTRY_USER required}"
: "${REGISTRY_PASS:?REGISTRY_PASS required}"
: "${REGISTRY_CA_FILE:=pb/docker/registry/certs/rootca.crt}"
: "${REG_SCHEME:=https}"

if [[ "${REGISTRY_CA_FILE}" != /* ]]; then
  REGISTRY_CA_FILE="${PROJECT_ROOT}/${REGISTRY_CA_FILE#./}"
fi

[[ -n "$KUBE_CONTEXT" ]] || die "Unable to resolve kubectl context."
req docker
req kubectl
normalize_bool_var API_CERT
normalize_bool_var OPEN_PORTS
normalize_bool_var ETCD_EXPOSURE
normalize_bool_var ANONYMOUS_AUTH
require_port_var REGISTRY_PORT
REG_SCHEME="${REG_SCHEME,,}"
case "$REG_SCHEME" in
  https|http) ;;
  *) die "Invalid REG_SCHEME='${REG_SCHEME}' (allowed: https|http)." ;;
esac

# --------------------------
# 1) Count nodes and ensure total >= 3
# --------------------------
log "Using kubectl context: $KUBE_CONTEXT"

mapfile -t ALL_NODES < <(kubectl_ctx get nodes -o name | sed 's#node/##')
TOTAL_NODES=${#ALL_NODES[@]}
log "Total nodes found: $TOTAL_NODES"

if (( TOTAL_NODES < 3 )); then
  die "Total nodes less than 3 (found $TOTAL_NODES). Aborting."
fi

# --------------------------
# 2) Identify control-plane(s) and workers and extract container runtime info
# --------------------------
mapfile -t CONTROL_PLANE_NODES < <(kubectl_ctx get nodes -l 'node-role.kubernetes.io/control-plane' -o name 2>/dev/null || true)
if [[ ${#CONTROL_PLANE_NODES[@]} -eq 0 ]]; then
  mapfile -t CONTROL_PLANE_NODES < <(kubectl_ctx get nodes -l 'node-role.kubernetes.io/master' -o name 2>/dev/null || true)
fi
for i in "${!CONTROL_PLANE_NODES[@]}"; do CONTROL_PLANE_NODES[$i]="${CONTROL_PLANE_NODES[$i]#node/}"; done

WORKER_NODES=()
for n in "${ALL_NODES[@]}"; do
  skip=false
  for cp in "${CONTROL_PLANE_NODES[@]}"; do
    if [[ "$n" == "$cp" ]]; then skip=true; break; fi
  done
  $skip || WORKER_NODES+=("$n")
done

if [[ ${#CONTROL_PLANE_NODES[@]} -eq 0 ]]; then
  die "No control-plane/master node found by label 'control-plane' or 'master'."
fi

if [[ ${#WORKER_NODES[@]} -eq 0 ]]; then
  die "No worker nodes found."
fi

log "Control-plane node(s): ${CONTROL_PLANE_NODES[*]}"
log "Worker nodes: ${WORKER_NODES[*]}"

# Pick the first control-plane node as canonical
CONTROL_PLANE_NODE="${CONTROL_PLANE_NODES[0]}"
CP_IP="$(node_internal_ip "$CONTROL_PLANE_NODE")"
if [[ -z "$CP_IP" ]]; then die "Could not determine IPv4 InternalIP of control-plane node $CONTROL_PLANE_NODE."; fi   # <<< CHANGED
log "Chosen control-plane node: $CONTROL_PLANE_NODE with IPv4 InternalIP $CP_IP"
export CONTROL_PLANE_NODE

# Map control-plane InternalIP -> docker container & network
FOUND="$(find_container_by_ip "$CP_IP" || true)"
if [[ -z "$FOUND" ]]; then
  die "Could not find a Docker container with IPv4 $CP_IP. Is this a kind/minikube (Docker driver) cluster?"
fi
CP_CONTAINER="$(echo "$FOUND" | cut -d';' -f1)"
CP_NETWORKS="$(echo "$FOUND" | cut -d';' -f2-)"
CP_NETWORK="$(echo "$CP_NETWORKS" | awk '{print $1}')"
export CP_NETWORK

# Extract CRI runtime socket path from /etc/crictl.yaml inside control-plane
CRICTL_RUNTIME_PATH="$(docker exec -i "$CP_CONTAINER" sh -lc "sed -n 's/^runtime-endpoint:[[:space:]]*//p' /etc/crictl.yaml 2>/dev/null | head -n1" || true)"
CRICTL_RUNTIME_PATH="${CRICTL_RUNTIME_PATH#unix://}"
if [[ -z "$CRICTL_RUNTIME_PATH" ]]; then
  warn "Could not read runtime-endpoint from /etc/crictl.yaml in $CP_CONTAINER."
  CRICTL_RUNTIME_PATH="/run/containerd/containerd.sock"
else
  log "Control-plane CRI runtime socket path: $CRICTL_RUNTIME_PATH"
fi
export CRICTL_RUNTIME_PATH

log "Control-plane container: $CP_CONTAINER"
log "Control-plane docker network(s): $CP_NETWORKS"
log "Primary docker network chosen: $CP_NETWORK"

# --------------------------
# 3) Label control-plane container and copy apiserver certs
# --------------------------
if (( ${#WORKER_NODES[@]} < 2 )); then
  die "Need at least 2 worker nodes to label but found ${#WORKER_NODES[@]}."
fi

log "Patching kube-apiserver to exclude application pods..."
kubectl_ctx taint nodes "$CONTROL_PLANE_NODE" node-role.kubernetes.io/control-plane=:NoSchedule --overwrite=true

mkdir -p "$OUT_DIR"
if is_true "$API_CERT"; then
  log "Copying apiserver.crt and apiserver.key from $CP_CONTAINER to $OUT_DIR"
  if docker cp "$CP_CONTAINER":/etc/kubernetes/pki/apiserver.crt "$OUT_DIR/apiserver.crt" >/dev/null 2>&1 && \
     docker cp "$CP_CONTAINER":/etc/kubernetes/pki/apiserver.key "$OUT_DIR/apiserver.key" >/dev/null 2>&1; then
    log "Certificates copied to $OUT_DIR"
  else
    log "Failed to copy apiserver certs from $CP_CONTAINER. The path /etc/kubernetes/pki does not exist. Trying /var/lib/minikube/certs ..."
    if docker cp "$CP_CONTAINER":/var/lib/minikube/certs/apiserver.crt "$OUT_DIR/apiserver.crt" >/dev/null 2>&1 && \
       docker cp "$CP_CONTAINER":/var/lib/minikube/certs/apiserver.key "$OUT_DIR/apiserver.key" >/dev/null 2>&1; then
      log "Certificates copied to $OUT_DIR"
    else
      warn "Failed to copy apiserver certs from $CP_CONTAINER. The path may not exist or container may not expose them."
    fi
  fi
fi

# --------------------------
# 4) Determine docker network IPv4 subnet and construct IP with .200
# --------------------------
log "Inspecting Docker network '$CP_NETWORK' for IPv4 subnet."
NET_JSON="$(docker network inspect "$CP_NETWORK" --format '{{json .IPAM.Config}}' 2>/dev/null || true)"
[[ -n "$NET_JSON" ]] || die "docker network inspect returned nothing for network $CP_NETWORK. Cannot compute IP."

# Extract all subnets and pick the first IPv4 (no ':')
mapfile -t SUBNETS < <(printf '%s' "$NET_JSON" | grep -oE '"Subnet":"[^"]+"' | cut -d':' -f2 | tr -d '"')
IPV4_SUBNET=""
for s in "${SUBNETS[@]}"; do
  [[ "$s" == */* && "$s" != *:* ]] && { IPV4_SUBNET="$s"; break; }
done
[[ -n "$IPV4_SUBNET" ]] || die "No IPv4 subnet found on network $CP_NETWORK (found: ${SUBNETS[*]:-none}). Enable IPv4 on your kind/minikube network."

log "Detected IPv4 subnet for $CP_NETWORK: $IPV4_SUBNET"

BASE_IP="${IPV4_SUBNET%%/*}"  # e.g., 172.18.0.0
IFS='.' read -r o1 o2 o3 o4 <<< "$BASE_IP" || die "Unexpected IPv4 subnet base: $BASE_IP"
FRONTEND_PROXY_IP="${o1}.${o2}.${o3}.200"
GENERIC_SVC_ADDR="${o1}.${o2}.${o3}.201"
log "Constructed frontend-proxy IPv4: $FRONTEND_PROXY_IP (.200). And generic service IPv4: $GENERIC_SVC_ADDR (.201)"
export FRONTEND_PROXY_IP GENERIC_SVC_ADDR

# --------------------------
# 5) Build IP - HOST lines and write to file (control-plane, worker1..N)
# --------------------------
mkdir -p "$ATTACKER_DIR"
IPHOST_FILE="$ATTACKER_DIR/iphost"
log "Building IP - HOST file at $IPHOST_FILE"

{
  # Control planes: first is "control-plane", others "control-plane2", ...
  if (( ${#CONTROL_PLANE_NODES[@]} > 0 )); then
    idx=0
    for n in "${CONTROL_PLANE_NODES[@]}"; do
      ipn="$(node_internal_ip "$n")"
      if [[ -z "$ipn" ]]; then
        warn "No IPv4 InternalIP for control-plane node $n — skipping in iphost file."
        continue
      fi
      if (( idx == 0 )); then
        printf '%s - control-plane\n' "$ipn"
      else
        printf '%s - control-plane%d\n' "$ipn" "$((idx+1))"
      fi
      idx=$((idx+1))
    done
  fi

  # Workers: sorted for stable numbering (worker1..workerN)
  if (( ${#WORKER_NODES[@]} > 0 )); then
    mapfile -t SORTED_WORKERS < <(printf '%s\n' "${WORKER_NODES[@]}" | sort -V)
    widx=1
    for n in "${SORTED_WORKERS[@]}"; do
      ipn="$(node_internal_ip "$n")"
      if [[ -z "$ipn" ]]; then
        warn "No IPv4 InternalIP for worker node $n — skipping in iphost file."
        continue
      fi
      printf '%s - worker%d\n' "$ipn" "$widx"
      widx=$((widx+1))
    done
  fi
} > "$IPHOST_FILE"

log "Wrote iphost file:"
sed -n '1,200p' "$IPHOST_FILE"

# --------------------------
# 6) Enable kubelet read-only port on all worker nodes (address=0.0.0.0, readOnlyPort=10255)
# --------------------------

enable_ro_port_on_worker() {
  local node="$1"
  local ip cid name nets

  ip="$(node_internal_ip "$node")"
  if [[ -z "$ip" ]]; then
    warn "Could not get IPv4 InternalIP for $node — skipping."
    return 0
  fi

  FOUND="$(find_container_by_ip "$ip" || true)"
  if [[ -z "$FOUND" ]]; then
    warn "No Docker container found for node $node (IP $ip) — skipping."
    return 0
  fi

  name="$(echo "$FOUND" | cut -d';' -f1)"
  log "Patching kubelet on worker $node (container: $name, IP: $ip)..."

  docker exec -i "$name" bash -lc '
set -euo pipefail
cfg="/var/lib/kubelet/config.yaml"
dropin_dir="/etc/systemd/system/kubelet.service.d"
dropin="$dropin_dir/20-roport.conf"

already_enabled() {
  # 1) Quick HTTP check (preferred)
  if command -v curl >/dev/null 2>&1; then
    code="$(curl -sS -o /dev/null -m 2 -w "%{http_code}" http://127.0.0.1:10255/healthz || true)"
    if [[ "$code" == "200" || "$code" == "401" || "$code" == "403" ]]; then
      # kubelet responds — port is open (some builds require auth, hence 401/403)
      return 0
    fi
  fi
  # 2) Fallback: check if socket is listening
  if command -v ss >/dev/null 2>&1; then
    ss -ltn "( sport = :10255 )" 2>/dev/null | grep -q LISTEN && return 0
  elif command -v netstat >/dev/null 2>&1; then
    netstat -lnt 2>/dev/null | awk '"'"'{print $4}'"'"' | grep -q ":10255$" && return 0
  fi
  return 1
}

if already_enabled; then
  echo "kubelet read-only port (10255) is already enabled — nothing to do."
  exit 0
fi

echo "Enabling kubelet read-only port (10255)..."

if [[ -f "$cfg" ]]; then
  # Modify or add the "address" and "readOnlyPort" fields in kubelet config
  if grep -qE "^[[:space:]]*address:" "$cfg"; then
    sed -i -E "s|^[[:space:]]*address:.*$|address: \"0.0.0.0\"|" "$cfg"
  else
    printf "\naddress: \"0.0.0.0\"\n" >> "$cfg"
  fi

  if grep -qE "^[[:space:]]*readOnlyPort:" "$cfg"; then
    sed -i -E "s|^[[:space:]]*readOnlyPort:.*$|readOnlyPort: 10255|" "$cfg"
  else
    printf "readOnlyPort: 10255\n" >> "$cfg"
  fi

  systemctl daemon-reload || true
  systemctl restart kubelet
else
  # Fallback: use a systemd drop-in with KUBELET_EXTRA_ARGS (for legacy setups)
  mkdir -p "$dropin_dir"
  cat > "$dropin" <<EOF
[Service]
Environment="KUBELET_EXTRA_ARGS=--address=0.0.0.0 --read-only-port=10255"
EOF
  systemctl daemon-reload
  systemctl restart kubelet
fi

# Quick health check on port 10255 (read-only and unauthenticated)
sleep 2
curl -sS -o /dev/null -w "kubelet 10255 on localhost: %{http_code}\n" http://127.0.0.1:10255/healthz || true
' || warn "Failed to patch kubelet on $node (container $name). Check logs."
}

if is_true "$OPEN_PORTS"; then
  log "Enabling kubelet read-only port (10255) on all worker nodes..."
  for n in "${WORKER_NODES[@]}"; do
    enable_ro_port_on_worker "$n"
  done
  log "Finished configuring read-only port on worker nodes."
fi

# --------------------------
# 7) Open etcd port 12379
# --------------------------

NS=kube-system

# Detect the etcd pod (static pod mirror) and its nodeName
log "Detecting etcd pod and control-plane node..."
ETCD_POD="$(kubectl_ctx -n "$NS" get pods -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}' | grep '^etcd-' | head -n1 || true)"
if [[ -z "${ETCD_POD:-}" ]]; then
  die "No etcd pod found in namespace ${NS}."
fi
NODE_NAME="$(kubectl_ctx -n "$NS" get pod "$ETCD_POD" -o jsonpath='{.spec.nodeName}')"
log "etcd pod: $ETCD_POD  |  nodeName: $NODE_NAME"

# Find the Docker container corresponding to the node
#   Heuristic: name matches nodeName (minikube), or partial match in container names.
log "Looking for the Docker container of the node..."
if docker inspect "$NODE_NAME" >/dev/null 2>&1; then
  NODE_CTN="$NODE_NAME"
elif [[ "$NODE_NAME" == "$CONTROL_PLANE_NODE" && -n "${CP_CONTAINER:-}" ]]; then
  NODE_CTN="$CP_CONTAINER"
else
  NODE_CTN="$(docker ps --format '{{.Names}}' | grep -Fx "$NODE_NAME" | head -n1 || true)"
  if [[ -z "${NODE_CTN:-}" ]]; then
    NODE_CTN="$(docker ps --format '{{.Names}}' | grep -E 'control-plane|minikube' | head -n1 || true)"
  fi
fi
if [[ -z "${NODE_CTN:-}" ]]; then
  die "Cannot map ${NODE_NAME} to a Docker container. Specify it manually with NODE_CTN=..."
fi
log "Node container: $NODE_CTN"

PATCH_CMD=$(cat <<'EOF'
set -euo pipefail
mf="/etc/kubernetes/manifests/etcd.yaml"

port_open() {
  # 1) Try HTTP health (preferred)
  if command -v curl >/dev/null 2>&1; then
    code="$(curl -sS -m 2 -o /dev/null -w "%{http_code}" http://127.0.0.1:12379/health || true)"
    # etcd /health returns 200 if healthy; even a 404/405 means the port is reachable
    [[ "$code" =~ ^(200|204|301|302|400|401|403|404|405)$ ]] && return 0
  fi
  # 2) Fallback: check socket
  if command -v ss >/dev/null 2>&1; then
    ss -ltn "( sport = :12379 )" 2>/dev/null | grep -q LISTEN && return 0
  elif command -v netstat >/dev/null 2>&1; then
    netstat -lnt 2>/dev/null | awk '"'"'{print $4}'"'"' | grep -q ":12379$" && return 0
  fi
  return 1
}

if port_open; then
  echo "etcd client port 12379 already open — skipping manifest patch."
  exit 0
fi

if grep -q -- '--listen-client-urls=' "$mf"; then
  sed -i -E '/--listen-client-urls=/ s#(--listen-client-urls=).*#\1https://127.0.0.1:2379,http://0.0.0.0:12379#' "$mf"
else
  sed -i -E '/^[[:space:]]*- --data-dir=/a\    - --listen-client-urls=https:\/\/127.0.0.1:2379,http:\/\/0.0.0.0:12379' "$mf"
fi
EOF
)

if is_true "$ETCD_EXPOSURE"; then
  log "Applying patch inside the node container..."
  docker exec -i "$NODE_CTN" bash -lc "$PATCH_CMD"
fi

# --------------------------
# 8) Insert anonymous authentication in kube-apiserver
# --------------------------

: "${NODE_CTN:?NODE_CTN must be set (control-plane node container name)}"
APISERVER_WAIT_TIMEOUT="${APISERVER_WAIT_TIMEOUT:-300}"  # seconds
ANONYMOUS_AUTH="${ANONYMOUS_AUTH:-false}"

# The patch script to run *inside* the node container.
# It enables --anonymous-auth=true in /etc/kubernetes/manifests/kube-apiserver.yaml if not already enabled.
PATCH_APISERVER_CMD=$(cat <<'EOF'
set -euo pipefail
mf="/etc/kubernetes/manifests/kube-apiserver.yaml"

already_enabled_by_manifest() {
  grep -q -- "--anonymous-auth=true" "$mf"
}

if already_enabled_by_manifest; then
  echo "kube-apiserver already accepts anonymous requests — skipping manifest patch."
  exit 0
fi

if grep -q -- '--anonymous-auth=' "$mf"; then
  sed -i -E 's#--anonymous-auth=(true|false)#--anonymous-auth=true#' "$mf"
else
  sed -i -E '/^[[:space:]]*- --authorization-mode=/a\    - --anonymous-auth=true' "$mf" || \
  sed -i -E '/^[[:space:]]*- --kubelet-client-certificate=/a\    - --anonymous-auth=true' "$mf"
fi
echo "Manifest updated: --anonymous-auth=true added/enforced."
EOF
)

# --- Helpers ---
_get_apiserver_identity() {
  # Returns "podName|podUID" or empty if API is not reachable
  kubectl_ctx -n kube-system get pod -l component=kube-apiserver \
    -o jsonpath='{.items[0].metadata.name}{"|"}{.items[0].metadata.uid}' 2>/dev/null || true
}

_wait_api_reachable_short() {
  local deadline=$(( $(date +%s) + 10 ))
  while true; do
    if kubectl_ctx version --request-timeout=5s >/dev/null 2>&1; then
      return 0
    fi
    (( $(date +%s) > deadline )) && return 1
    sleep 1
  done
}

_wait_new_apiserver_ready() {
  local prev_uid="$1"
  local deadline=$(( $(date +%s) + APISERVER_WAIT_TIMEOUT ))
  while true; do
    _wait_api_reachable_short || true

    local id pod uid
    id="$(_get_apiserver_identity)"
    pod="${id%%|*}"
    uid="${id##*|}"

    if [[ -n "$pod" && -n "$uid" && "$uid" != "$prev_uid" ]]; then
      log "Detected new kube-apiserver pod: $pod (UID changed). Waiting for Ready..."
      if kubectl_ctx -n kube-system wait --for=condition=Ready "pod/$pod" --timeout=180s >/dev/null 2>&1; then
        log "kube-apiserver is Ready: $pod"
        return 0
      else
        warn "New kube-apiserver pod did not become Ready within 180s; continuing until overall timeout."
      fi
    fi

    (( $(date +%s) > deadline )) && {
      err "Timeout (${APISERVER_WAIT_TIMEOUT}s) waiting for kube-apiserver to be recreated and Ready."
      echo "=== kubectl get pods -n kube-system -o wide (apiserver) ==="
      kubectl_ctx -n kube-system get pod -o wide | grep -i apiserver || true
      [[ -n "$pod" ]] && { echo "=== describe $pod ==="; kubectl_ctx -n kube-system describe pod "$pod" || true; }
      echo "=== last 200 lines of $NODE_CTN logs ==="
      docker logs --tail 200 "$NODE_CTN" 2>/dev/null || true
      return 1
    }

    sleep 2
  done
}

# --- Main: patch + conditional wait ---
if is_true "$ANONYMOUS_AUTH"; then
  # Capture current apiserver UID (if API is reachable)
  prev_id="$(_get_apiserver_identity)"
  prev_uid="${prev_id##*|}"

  log "Patching kube-apiserver manifest inside the node container..."
  # Run the patch script inside the node container and capture output safely
  PATCH_OUT="$(docker exec -i "$NODE_CTN" bash -lc "$PATCH_APISERVER_CMD" 2>&1 || true)"
  printf "%s\n" "$PATCH_OUT"

  # If the patch was a no-op, skip the wait (no restart expected)
  if printf "%s" "$PATCH_OUT" | grep -qi "already accepts anonymous requests"; then
    log "Patch skipped (already enabled). Not waiting for restart."
  else
    log "Waiting for the kube-apiserver to restart and become Ready..."
    _wait_new_apiserver_ready "$prev_uid" || die "kube-apiserver did not become Ready after patch."
  fi
else
  warn "ANONYMOUS_AUTH is not 'true' — skipping kube-apiserver patch."
fi

log "Done."

# --------------------------
# 9) Generation of certificates
# --------------------------
rm -f "./pb/docker/registry/htpasswd"
log "Generating htpasswd file..."
mkdir -p "$(dirname "./pb/docker/registry/htpasswd")"
htpasswd -Bbn "$REGISTRY_USER" "$REGISTRY_PASS" > "./pb/docker/registry/htpasswd"

CERTSDIR="./pb/docker/registry/certs"
mkdir -p "$CERTSDIR"

# 1) Root CA
if [ ! -f "$CERTSDIR/rootca.crt" ] || [ ! -f "$CERTSDIR/rootca.key" ]; then
  log "Generating root CA (rootca.crt/rootca.key) ..."
  openssl genrsa -out "$CERTSDIR/rootca.key" 4096
  openssl req -x509 -new -nodes -key "$CERTSDIR/rootca.key" -sha256 -days 3650 \
    -subj "/CN=registry-rootca" \
    -addext "basicConstraints=critical,CA:true" \
    -addext "keyUsage=critical,keyCertSign,cRLSign" \
    -out "$CERTSDIR/rootca.crt"
else
  warn "Root CA already exists, no action needed"
fi

# 2) Server key/cert signed by CA, with SAN = DNS:${REGISTRY_NAME}
if [ ! -f "$CERTSDIR/domain.crt" ] || [ ! -f "$CERTSDIR/domain.key" ]; then
  log "Generating server TLS certificate signed by root CA (SAN=DNS:${REGISTRY_NAME}) ..."
  openssl genrsa -out "$CERTSDIR/domain.key" 4096
  openssl req -new -key "$CERTSDIR/domain.key" -subj "/CN=${REGISTRY_NAME}" -out "$CERTSDIR/domain.csr"

  tmp_ext="$(mktemp)"
  cat > "$tmp_ext" <<EOF
subjectAltName=DNS:${REGISTRY_NAME}
extendedKeyUsage=serverAuth
keyUsage=digitalSignature,keyEncipherment
EOF

  openssl x509 -req -in "$CERTSDIR/domain.csr" \
    -CA "$CERTSDIR/rootca.crt" -CAkey "$CERTSDIR/rootca.key" -CAcreateserial \
    -out "$CERTSDIR/domain.crt" -days 825 -sha256 -extfile "$tmp_ext"

  rm -f "$CERTSDIR/domain.csr" "$tmp_ext" "$CERTSDIR/rootca.srl"
else
  warn "TLS server certificate already exists, no action needed"
fi

# --------------------------
# 10) Adding CA + AUTH for registry to the nodes
# --------------------------
REG_HOSTPORT="${REGISTRY_NAME}:${REGISTRY_PORT}"

install_on_node_container() {
  local node="$1"
  local ip pair name auth_b64 desired_hosts_toml current_hosts_toml
  local hosts_changed ca_cert_b64

  ip="$(node_internal_ip "$node")" || return 1
  [ -n "$ip" ] || { warn "No IPv4 for $node"; return 1; }

  pair="$(find_container_by_ip "$ip" || true)"
  if [ -z "$pair" ]; then
    warn "Node $node is not a local Docker container (skipping)."
    return 0
  fi

  name="${pair%%;*}"
  log "Configuring registry trust+auth for ${REG_HOSTPORT} on node ${name}"

  # Adding/updating registry auth in containerd hosts.toml.
  auth_b64="$(printf '%s' "${REGISTRY_USER}:${REGISTRY_PASS}" | base64 | tr -d '\n')"
  desired_hosts_toml="$(cat <<EOF
[host."${REG_SCHEME}://${REG_HOSTPORT}"]
capabilities = ["pull", "resolve"]
[host."${REG_SCHEME}://${REG_HOSTPORT}".header]
Authorization = "Basic ${auth_b64}"
EOF
)"

  current_hosts_toml="$(docker exec "$name" sh -lc "cat '/etc/containerd/certs.d/${REG_HOSTPORT}/hosts.toml' 2>/dev/null" || true)"
  if [[ "${current_hosts_toml}" == "${desired_hosts_toml}" ]]; then
    hosts_changed="false"
  else
    hosts_changed="true"
  fi

  ca_cert_b64=""
  if [[ "${REG_SCHEME}" == "https" ]]; then
    if [[ -r "${REGISTRY_CA_FILE}" ]]; then
      ca_cert_b64="$(base64 < "${REGISTRY_CA_FILE}" | tr -d '\n')"
    else
      warn "CA file '${REGISTRY_CA_FILE}' not readable; TLS may fail."
    fi
  fi

  if ! docker exec -i "${name}" sh -s -- \
    "${REG_HOSTPORT}" "${REG_SCHEME}" "${REGISTRY_NAME}" "${auth_b64}" "${hosts_changed}" "${ca_cert_b64}" <<'EOF'
set -eu
reg_hostport="$1"
reg_scheme="$2"
reg_name="$3"
auth_b64="$4"
hosts_changed="$5"
ca_cert_b64="$6"

certsd="/etc/containerd/certs.d/${reg_hostport}"
dockerd="/etc/docker/certs.d/${reg_hostport}"
hosts_dir="/usr/local/share/ca-certificates"
hosts_toml="${certsd}/hosts.toml"
ca_installed="false"
ca_tmp_file="/tmp/registry-ca.crt"

mkdir -p "${certsd}" "${hosts_dir}"

if [ "${reg_scheme}" = "https" ] && [ -n "${ca_cert_b64}" ]; then
  printf '%s' "${ca_cert_b64}" | base64 -d > "${ca_tmp_file}"
  mkdir -p "${dockerd}"
  install -D -m 0644 "${ca_tmp_file}" "${certsd}/ca.crt"
  install -D -m 0644 "${ca_tmp_file}" "${dockerd}/ca.crt"
  install -D -m 0644 "${ca_tmp_file}" "${hosts_dir}/registry-${reg_name}.crt"
  rm -f "${ca_tmp_file}"
  update-ca-certificates >/dev/null 2>&1 || true
  ca_installed="true"
fi

if [ "${hosts_changed}" = "true" ]; then
  umask 077
  cat > "${hosts_toml}" <<HOSTS
[host."${reg_scheme}://${reg_hostport}"]
capabilities = ["pull", "resolve"]
[host."${reg_scheme}://${reg_hostport}".header]
Authorization = "Basic ${auth_b64}"
HOSTS
fi

if [ "${hosts_changed}" = "true" ] || [ "${ca_installed}" = "true" ]; then
  systemctl restart containerd >/dev/null 2>&1 || true
  systemctl restart docker >/dev/null 2>&1 || true
  systemctl restart cri-docker >/dev/null 2>&1 || true
fi
EOF
  then
    err "docker exec failed while configuring registry trust/auth on ${name}"
    return 1
  fi

  if [[ "${hosts_changed}" == "true" ]]; then
    log "Updated /etc/containerd/certs.d/${REG_HOSTPORT}/hosts.toml in node ${name}"
  else
    log "hosts.toml already up to date on node ${name}"
  fi

  if [[ "${REG_SCHEME}" == "https" && -z "${ca_cert_b64}" && ! -r "${REGISTRY_CA_FILE}" ]]; then
    warn "CA file unavailable while REG_SCHEME=https for node ${name}."
  fi

  if [[ "${REG_SCHEME}" == "http" ]]; then
    log "Configured node ${name} to use HTTP registry endpoint ${REG_HOSTPORT}."
  fi

  return 0
}

log "Adding registry CA/auth to all nodes for ${REG_HOSTPORT} ..."
failed_nodes=()
for n in "${ALL_NODES[@]}"; do
  if ! install_on_node_container "$n"; then
    warn "Registry setup failed on $n"
    failed_nodes+=("$n")
  fi
done

if (( ${#failed_nodes[@]} > 0 )); then
  warn "Retrying registry setup after 60s for nodes: ${failed_nodes[*]}"
  sleep 60

  retry_failed_nodes=()
  for n in "${failed_nodes[@]}"; do
    if ! install_on_node_container "$n"; then
      warn "Registry setup failed again on $n"
      retry_failed_nodes+=("$n")
    fi
  done

  if (( ${#retry_failed_nodes[@]} > 0 )); then
    err "Registry setup still failing on: ${retry_failed_nodes[*]}"
  fi
fi
log "Registry CA/auth installation step completed."
