#!/bin/bash

### === Utility functions ===
log()  { printf "\033[1;36m[INFO]\033[0m %s\n" "$*"; }
warn() { printf "\033[1;33m[WARN]\033[0m %s\n" "$*"; }
err()  { printf "\033[1;31m[ERR ]\033[0m %s\n" "$*" >&2; }
die()  { echo -e "[ERROR] $*" >&2; exit 1; }

ensure_registry_hosts() {
  local ip="127.0.0.1"
  local hosts="/etc/hosts"

  if grep -Eq "^[[:space:]]*${ip//./\\.}[[:space:]]+${REGISTRY_NAME}([[:space:]]|\$)" "$hosts"; then
    warn "${REGISTRY_NAME} is already mapped: ${ip} in ${hosts}"
    return 0
  fi

  # if exists one line for 'registry' with another IP, substituting
  if grep -Eq "^[[:space:]]*[0-9.:a-fA-F]+[[:space:]]+${REGISTRY_NAME}([[:space:]]|\$)" "$hosts"; then
    log "Updating existing mapping for ${REGISTRY_NAME} in ${hosts}"
    sudo sed -i.bak -E "s|^[[:space:]]*[0-9.:a-fA-F]+[[:space:]]+(${REGISTRY_NAME})([[:space:]]|\$)|${ip}\t\1\2|" "$hosts"
  else
    log "Adding mapping ${REGISTRY_NAME} -> ${ip} in ${hosts}"
    echo -e "${ip}\t${REGISTRY_NAME}" | sudo tee -a "$hosts" >/dev/null
  fi
}

wait_registry_ready() {
  # Also 401 is "Ready"
  local host="$1" port="$2" timeout="${3:-60}"
  local i=0 code=000
  while [[ $i -lt $timeout ]]; do
    code="$(curl -sk -o /dev/null -w '%{http_code}' "https://${host}:${port}/v2/")" || true
    if [[ "$code" == "200" || "$code" == "401" ]]; then
      return 0
    fi
    sleep 1; i=$((i+1))
  done
  echo "Timeout: registry ${host}:${port} not ready (last HTTP: ${code})" >&2
  return 1
}

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
  local ip="$1"
  while IFS= read -r cid; do
    local name ips nets
    name="$(docker inspect -f '{{.Name}}' "$cid" | sed 's#^/##')"
    ips="$(docker inspect -f '{{range $k,$v := .NetworkSettings.Networks}}{{$v.IPAddress}} {{end}}' "$cid" | xargs || true)"
    nets="$(docker inspect -f '{{range $k,$v := .NetworkSettings.Networks}}{{$k}} {{end}}' "$cid" | xargs || true)"
    for tip in $ips; do
      if [[ "$tip" == "$ip" ]]; then
        printf '%s;%s' "$name" "$nets"
        return 0
      fi
    done
  done < <(docker ps -q)
  return 1
}

# --------------
# Config / env
# --------------
: "${KUBE_CONTEXT:=$(kubectl config current-context)}"
: "${OUT_DIR:=./pb/docker/attacker/apiserver}"
: "${ATTACKER_DIR:=./pb/docker/attacker}"
: "${ETCD_DOCKER_IMAGE:=quay.io/coreos/etcd:v3.5.10}"
: "${PLAIN_PORT:=12379}"

# --------------------------
# 1) Count nodes and ensure total >= 3
# --------------------------
log "Using kubectl context: $KUBE_CONTEXT"

ALL_NODES=($(kubectl_ctx get nodes -o name | sed 's#node/##'))
TOTAL_NODES=${#ALL_NODES[@]}
log "Total nodes found: $TOTAL_NODES"

if (( TOTAL_NODES < 3 )); then
  die "Total nodes less than 3 (found $TOTAL_NODES). Aborting."
fi

# --------------------------
# 2) Identify control-plane(s) and workers
# --------------------------
CONTROL_PLANE_NODES=($(kubectl_ctx get nodes -l 'node-role.kubernetes.io/control-plane' -o name 2>/dev/null || true))
if [[ ${#CONTROL_PLANE_NODES[@]} -eq 0 ]]; then
  CONTROL_PLANE_NODES=($(kubectl_ctx get nodes -l 'node-role.kubernetes.io/master' -o name 2>/dev/null || true))
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
kubectl taint nodes $CONTROL_PLANE_NODE node-role.kubernetes.io/control-plane=:NoSchedule --overwrite=true

mkdir -p "$OUT_DIR"
if [[ "$API_CERT" == "true" ]]; then
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
log "Constructed frontend-proxy IPv4: $FRONTEND_PROXY_IP (.200)."
export FRONTEND_PROXY_IP

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

if [[ "$OPEN_PORTS" == "true" ]]; then
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
ETCD_POD="$(kubectl -n "$NS" get pods -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}' | grep '^etcd-' | head -n1)"
if [[ -z "${ETCD_POD:-}" ]]; then
  echo "Error: no etcd pod found in $NS" >&2; exit 1
fi
NODE_NAME="$(kubectl -n "$NS" get pod "$ETCD_POD" -o jsonpath='{.spec.nodeName}')"
log "etcd pod: $ETCD_POD  |  nodeName: $NODE_NAME"

# Find the Docker container corresponding to the node
#   Heuristic: name matches nodeName (minikube), or partial match in container names.
log "Looking for the Docker container of the node..."
if docker inspect "$NODE_NAME" >/dev/null 2>&1; then
  NODE_CTN="$NODE_NAME"
else
  NODE_CTN="$(docker ps --format '{{.Names}}' | grep -E "^${NODE_NAME}$|${NODE_NAME}|control-plane" | head -n1 || true)"
fi
if [[ -z "${NODE_CTN:-}" ]]; then
  echo "Error: cannot map $NODE_NAME to a Docker container. Specify it manually with NODE_CTN=…" >&2
  exit 1
fi
log "Node container: $NODE_CTN"

# Patch the etcd static manifest
ETCD_MANIFEST="/etc/kubernetes/manifests/etcd.yaml"
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

if [[ "$ETCD_EXPOSURE" == "true" ]]; then
  log "Applying patch inside the node container..."
  docker exec -i "$NODE_CTN" bash -lc "$PATCH_CMD"
fi

# --------------------------
# 8) Insert anonymous authentication
# --------------------------

log "Enabling anonymous-auth on kube-apiserver..."

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
EOF
)

if [[ "$ANONYMOUS_AUTH" == "true" ]]; then
  log "Patching kube-apiserver inside the node container..."
  docker exec -i "$NODE_CTN" bash -lc "$PATCH_APISERVER_CMD"
fi

# Wait for kubelet to recreate the API server pod
log "Waiting for changes to apply..."
sleep 60

log "Done."

# --------------------------
# 9) Generation of certificates and route for host
# --------------------------
if [ ! -f "./pb/docker/registry/htpasswd" ]; then
  log "Generating htpasswd file..."
  mkdir -p "$(dirname "./pb/docker/registry/htpasswd")"
  htpasswd -Bbn "$REGISTRY_USER" "$REGISTRY_PASS" > "./pb/docker/registry/htpasswd"
else
  warn "File htpasswd already existing, no action needed"
fi

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


ensure_registry_hosts

# --------------------------
# 10) Adding CA + AUTH for registry to the nodes
# --------------------------
REGISTRY_CA_FILE="${REGISTRY_CA_FILE:-pb/docker/registry/certs/rootca.crt}"
: "${REGISTRY_NAME:?REGISTRY_NAME required}"
: "${REGISTRY_PORT:?REGISTRY_PORT required}"
: "${REGISTRY_USER:?REGISTRY_USER required}"
: "${REGISTRY_PASS:?REGISTRY_PASS required}"

REG_HOSTPORT="${REGISTRY_NAME}:${REGISTRY_PORT}"
REG_SCHEME="${REG_SCHEME:-https}"

install_on_node_container() {
  local node="$1"
  local ip pair name gw certsd hosts_dir tmp_ht tmp_remote changed=0 changed_os_ca=0 changed_ctrd_ca=0 changed_hosts=0 changed_hosts_toml=0

  ip="$(node_internal_ip "$node")" || return 1
  [ -n "$ip" ] || { warn "No IPv4 for $node"; return 1; }

  pair="$(find_container_by_ip "$ip" || true)"
  if [ -z "$pair" ]; then
    warn "Node $node is not a local Docker container (skipping)."
    return 0
  fi

  name="${pair%%;*}"
  certsd="/etc/containerd/certs.d/${REG_HOSTPORT}"
  hosts_dir="/usr/local/share/ca-certificates"
  log "Configuring registry trust+auth for ${REG_HOSTPORT} on node ${name}"

  # ensure dirs
  docker exec "$name" sh -lc "mkdir -p '${certsd}' '${hosts_dir}'"

  # if HTTPS, install CA as both containerd trust and OS trust
  if [ "${REG_SCHEME}" = "https" ]; then
    if [ -r "$REGISTRY_CA_FILE" ]; then
      docker cp "$REGISTRY_CA_FILE" "${name}:${certsd}/ca.crt"
      docker cp "$REGISTRY_CA_FILE" "${name}:${hosts_dir}/registry-${REGISTRY_NAME}.crt"
      docker exec "$name" sh -lc "update-ca-certificates >/dev/null 2>&1 || true"
    else
      warn "CA file '$REGISTRY_CA_FILE' not readable; TLS may fail."
    fi
  fi

  # Adding registry auth to containerd
  if ! docker exec "$name" sh -lc "test -f '${certsd}/hosts.toml'"; then
    cat <<EOF | docker exec -i "$name" sh -lc "umask 077; mkdir -p '${certsd}'; cat >'${certsd}/hosts.toml'"
[host."https://${REG_HOSTPORT}"]
capabilities = ["pull", "resolve"]
[host."https://${REG_HOSTPORT}".header]
Authorization = "Basic $(echo -n "${REGISTRY_USER}:${REGISTRY_PASS}" | base64)"
EOF
    changed_hosts_toml=1
    log "Created ${certsd}/hosts.toml in node ${name}"
    docker exec "$name" sh -lc 'systemctl restart containerd || true'
  else
    warn "hosts.toml already present on node ${name}, not touching it"
  fi
}

log "Adding registry CA/auth to all nodes for ${REG_HOSTPORT} ..."
for n in "${ALL_NODES[@]}"; do
  install_on_node_container "$n" || warn "Registry setup failed on $n"
done
log "Registry CA/auth installation step completed."

# --------------------------
# 11) Running docker compose and waiting for registry to login
# --------------------------
#sudo rm -rf pb/docker/attacker/results
#mkdir -p pb/docker/attacker/results
#KUBESERVER_PORT="$(kubectl -n default get endpoints kubernetes -o jsonpath='{.subsets[0].ports[0].port}' 2>/dev/null || echo 6443)"
#export KUBESERVER_PORT
#K8S_IMAGE="$(kubectl get pod -n kube-system -l component=kube-apiserver -o jsonpath='{.items[0].spec.containers[0].image}{"\n"}')"
#export K8S_IMAGE
#log "Running docker compose..."
#BUILD_FLAG=""
#if [[ "$BUILD_CONTAINERS_DOCKER" != "true" ]]; then BUILD_FLAG="--build"; fi
#docker compose -f ./pb/docker/docker-compose.yml up -d $BUILD_FLAG
#wait_registry_ready "${REGISTRY_NAME}" "${REGISTRY_PORT}" 300 || exit 1
#docker login ${REGISTRY_NAME}:${REGISTRY_PORT} -u "$REGISTRY_USER" -p "$REGISTRY_PASS"