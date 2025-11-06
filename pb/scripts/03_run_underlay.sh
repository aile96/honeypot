#!/usr/bin/env bash
set -euo pipefail

# === Logging utilities ===
log()  { printf "\033[1;36m[INFO]\033[0m %s\n" "$*"; }
warn() { printf "\033[1;33m[WARN]\033[0m %s\n" "$*"; }
err()  { printf "\033[1;31m[ERR ]\033[0m %s\n" "$*" >&2; }
die()  { printf "\033[1;31m[ERROR]\033[0m %s\n" "$*" >&2; exit 1; }

# === Defaults (adjust if needed) ===
CP_NETWORK=${CP_NETWORK:-minikube}
BUILD_CONTAINERS_DOCKER=${BUILD_CONTAINERS_DOCKER:-true}
IMAGE_VERSION=${IMAGE_VERSION:-2.0.2}
REGISTRY_NAME=${REGISTRY_NAME:-registry}
REGISTRY_PORT=${REGISTRY_PORT:-5000}
PROXY=${PROXY:-proxy}
CALDERA_SERVER=${CALDERA_SERVER:-caldera.dock}
CALDERA_CONTROLLER=${CALDERA_CONTROLLER:-caldera.cont}
ATTACKER=${ATTACKER:-caldera.outs}
CONTROL_PLANE_NODE=${CONTROL_PLANE_NODE:-kind-control-plane}
KUBESERVER_PORT=${KUBESERVER_PORT:-6443}
REGISTRY_USER=${REGISTRY_USER:-testuser}
REGISTRY_PASS=${REGISTRY_PASS:-testpassword}
MEM_NAMESPACE=${MEM_NAMESPACE:-mem}
DMZ_NAMESPACE=${DMZ_NAMESPACE:-dmz}
APP_NAMESPACE=${APP_NAMESPACE:-app}
PAY_NAMESPACE=${PAY_NAMESPACE:-pay}
FRONTEND_PROXY_IP=${FRONTEND_PROXY_IP:-127.0.0.1}
ADV_LIST=${ADV_LIST:-"KC1 – Image@cluster, KC2 – WiFi@outside, KC3 – FlagATT@outside, KC4 – CRSocket@outside, KC5 – Certificate@outside, KC6 – Etcd@outside"}

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
  printf 'Timeout: registry %s:%s not ready (last HTTP: %s)\n' "$host" "$port" "$code" >&2
  return 1
}

# Transform service name to uppercase ENV token (replace non-alnum with underscore)
svc_env_name() {
  local svc="$1"
  echo "$svc" | tr '[:lower:]' '[:upper:]' | sed 's/[^A-Z0-9]/_/g'
}

# Returns 0 if <SERVICE>_ENABLE exists and equals "true", else 1 (unset = disabled)
is_enabled() {
  local svc="$1"
  local varname
  varname="$(svc_env_name "$svc")_ENABLE"
  if [ "${!varname+set}" = "set" ]; then
    [[ "${!varname}" == "true" ]]
  else
    return 1
  fi
}

# Check if docker image exists locally
image_exists() {
  local img="$1"
  docker image inspect "$img" >/dev/null 2>&1
}

# Ensure Docker network exists (create if missing)
ensure_network() {
  local net="$1"
  if docker network ls --format '{{.Name}}' | grep -qx "$net"; then
    log "Docker network '$net' exists."
  else
    log "Docker network '$net' does not exist. Creating..."
    docker network create "$net"
  fi
}

# Generic runner: stop & remove existing container if present, then run detached
run_container() {
  local container_name="$1"; shift
  local run_args=("$@")
  if docker ps -a --format '{{.Names}}' | grep -qx "$container_name"; then
    log "Stopping and removing existing container: $container_name"
    docker rm -f "$container_name" || warn "Failed to remove existing container $container_name"
  fi
  log "Running container: $container_name"
  docker run -d "${run_args[@]}"
}

# Wait for a container to become healthy
wait_for_health() {
  local cname="$1"
  local timeout=${2:-60}
  local elapsed=0
  local interval=2

  log "Waiting up to ${timeout}s for container '${cname}' to be healthy..."
  while [ "$elapsed" -lt "$timeout" ]; do
    if ! docker ps -a --format '{{.Names}}' | grep -qx "$cname"; then
      warn "Container '$cname' not found while waiting for health."
      return 1
    fi
    local status
    status=$(docker inspect --format '{{.State.Health.Status}}' "$cname" 2>/dev/null || echo "no-health")
    if [ "$status" = "healthy" ]; then
      log "Container '${cname}' is healthy."
      return 0
    fi
    if [ "$status" = "unhealthy" ]; then
      warn "Container '${cname}' is unhealthy (docker reports 'unhealthy')."
    fi
    sleep "$interval"
    elapsed=$((elapsed + interval))
  done
  warn "Timed out waiting for '${cname}' to be healthy."
  return 1
}

# === SAFE ENV PASSING ===
# Collect env vars with a prefix into an ARRAY of proper docker -e arguments.
# Usage: collect_env_prefix_into "KC" KC_ARRAY
collect_env_prefix_into() {
  local prefix="$1"; local -n out="$2"
  out=()
  local var
  while IFS='=' read -r var _; do
    if [[ "$var" == ${prefix}* ]]; then
      # Pass as two args: -e  and VAR=value (no extra quoting; array keeps it intact)
      out+=("-e" "${var}=${!var}")
    fi
  done < <(env | sort)
}

# Validate image name to avoid accidental "1:latest" etc.
validate_image_or_die() {
  local img="$1"
  if [[ -z "$img" ]]; then
    die "Image name is empty."
  fi
  # Reject pure digits (optionally with tag): e.g. "1" or "1:latest"
  if [[ "$img" =~ ^[0-9]+(:[0-9A-Za-z._-]+)?$ ]]; then
    die "Suspicious image name '${img}' (looks numeric). Check your variable expansions."
  fi
}

# === MAIN ===
mkdir -p pb/docker/attacker/results

KUBESERVER_PORT="$(kubectl -n default get endpoints kubernetes -o jsonpath='{.subsets[0].ports[0].port}' 2>/dev/null || echo 6443)"
export KUBESERVER_PORT
K8S_IMAGE="$(kubectl get pod -n kube-system -l component=kube-apiserver -o jsonpath='{.items[0].spec.containers[0].image}{"\n"}' 2>/dev/null || true)"
export K8S_IMAGE

ensure_network "$CP_NETWORK"

# -------- registry --------
svc="registry"
REG_IMAGE="registry:2"
CONTAINER_NAME="${REGISTRY_NAME}"
HOSTNAME="${REGISTRY_NAME}"
validate_image_or_die "$REG_IMAGE"
run_container "${CONTAINER_NAME}" \
  --name "${CONTAINER_NAME}" \
  --hostname "${HOSTNAME}" \
  --restart always \
  --network "${CP_NETWORK}" \
  -p "${REGISTRY_PORT}:${REGISTRY_PORT}" \
  -e "REGISTRY_STORAGE_DELETE_ENABLED=true" \
  -e "REGISTRY_HTTP_ADDR=0.0.0.0:${REGISTRY_PORT}" \
  -e "REGISTRY_AUTH=htpasswd" \
  -e "REGISTRY_AUTH_HTPASSWD_REALM=Registry Realm" \
  -e "REGISTRY_AUTH_HTPASSWD_PATH=/auth/htpasswd" \
  -e "REGISTRY_HTTP_TLS_CERTIFICATE=/auth/certs/domain.crt" \
  -e "REGISTRY_HTTP_TLS_KEY=/auth/certs/domain.key" \
  -v "./pb/docker/registry:/auth:Z" \
  "${REG_IMAGE}"

# -------- load-generator --------
svc="load-generator"
if is_enabled "$svc"; then
  IMG_NAME="load-generator:${IMAGE_VERSION}"
  BUILD_CTX="./src/load-generator"
  DOCKERFILE="./src/load-generator/Dockerfile"
  if [[ "${BUILD_CONTAINERS_DOCKER}" == "true" ]] || ! image_exists "${IMG_NAME}"; then
    log "Building ${IMG_NAME} from ${BUILD_CTX} (dockerfile: ${DOCKERFILE})"
    docker build -t "${IMG_NAME}" -f "${DOCKERFILE}" "${BUILD_CTX}"
  else
    log "Image ${IMG_NAME} already present, skipping build."
  fi
  validate_image_or_die "$IMG_NAME"
  run_container "load-generator" \
    --name "load-generator" \
    --hostname "load-generator" \
    --restart unless-stopped \
    --network "${CP_NETWORK}" \
    -e "PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers" \
    -e "LOCUST_WEB_HOST=0.0.0.0" \
    -e "LOCUST_WEB_PORT=8089" \
    -e "LOCUST_USERS=20" \
    -e "LOCUST_SPAWN_RATE=10" \
    -e "LOCUST_HOST=http://${PROXY}:8080" \
    -e "LOCUST_HEADLESS=true" \
    -e "LOCUST_AUTOSTART=true" \
    -e "LOCUST_BROWSER_TRAFFIC_ENABLED=true" \
    "${IMG_NAME}"
else
  log "Skipping service 'load-generator' (env ${svc}_ENABLE != true)"
fi

# -------- samba --------
svc="samba"
if is_enabled "$svc"; then
  IMG_NAME="samba:${IMAGE_VERSION}"
  BUILD_CTX="./src/samba"
  DOCKERFILE="Dockerfile"
  if [[ "${BUILD_CONTAINERS_DOCKER}" == "true" ]] || ! image_exists "${IMG_NAME}"; then
    log "Building ${IMG_NAME} from ${BUILD_CTX}"
    docker build -t "${IMG_NAME}" -f "${BUILD_CTX}/${DOCKERFILE}" "${BUILD_CTX}"
  else
    log "Image ${IMG_NAME} already present, skipping build."
  fi
  validate_image_or_die "$IMG_NAME"
  run_container "samba-pv" \
    --name "samba-pv" \
    --hostname "samba-pv" \
    --restart unless-stopped \
    --network "${CP_NETWORK}" \
    -e "TZ=Europe/Rome" \
    -e "SAMBA_USER=k8s" \
    -e "SAMBA_PASS=password" \
    -e "SAMBA_UID=10001" \
    -e "SAMBA_GID=10001" \
    -e "SHARE_NAME=pvroot" \
    -e "SHARE_PATH=/share" \
    -e "HOSTS_ALLOW=127. 172.18.0. 172.19.0. 192.168." \
    -e "ENCRYPTION=required" \
    -e "LOG_LEVEL=1" \
    --cap-add NET_BIND_SERVICE --cap-add CHOWN \
    "${IMG_NAME}"
else
  log "Skipping service 'samba' (env ${svc}_ENABLE != true)"
fi

# -------- proxy (nginx) --------
svc="proxy"
if is_enabled "$svc"; then
  IMG_NAME="nginx:1.27-alpine"
  CONTAINER_NAME="${PROXY}"
  HOSTNAME="${PROXY}"
  validate_image_or_die "$IMG_NAME"
  run_container "${CONTAINER_NAME}" \
    --name "${CONTAINER_NAME}" \
    --hostname "${HOSTNAME}" \
    --restart unless-stopped \
    --network "${CP_NETWORK}" \
    -p "0.0.0.0:8888:8888" \
    -p "0.0.0.0:8080:8080" \
    -e "CALDERA_SERVER=${CALDERA_SERVER}" \
    -e "FRONTEND_PROXY=${FRONTEND_PROXY_IP}" \
    -e "NGINX_ENVSUBST_OUTPUT_DIR=/etc/nginx" \
    -v "./pb/docker/proxy/nginx.conf.template:/etc/nginx/templates/nginx.conf.template:ro" \
    -v "./pb/docker/proxy/conf.d-empty/:/etc/nginx/conf.d:ro" \
    "${IMG_NAME}"
else
  log "Skipping service 'proxy' (env ${svc}_ENABLE != true)"
fi

# -------- caldera-controller --------
svc="caldera-controller"
if is_enabled "$svc"; then
  IMG_NAME="caldera-controller:${IMAGE_VERSION}"
  BUILD_CTX="./src/caldera-controller"
  DOCKERFILE="./src/caldera-controller/Dockerfile"
  if [[ "${BUILD_CONTAINERS_DOCKER}" == "true" ]] || ! image_exists "${IMG_NAME}"; then
    log "Building ${IMG_NAME} from ${BUILD_CTX}"
    docker build -t "${IMG_NAME}" -f "${DOCKERFILE}" "${BUILD_CTX}"
  else
    log "Image ${IMG_NAME} already present, skipping build."
  fi
  validate_image_or_die "$IMG_NAME"

  # Collect KC-related envs (as arrays)
  KC_ENABLE_ENVS=(); collect_env_prefix_into "ENABLEKC" KC_ENABLE_ENVS
  KC_SCRIPT_PRE_ENVS=(); collect_env_prefix_into "SCRIPT_PRE_KC" KC_SCRIPT_PRE_ENVS
  KC_SCRIPT_POST_ENVS=(); collect_env_prefix_into "SCRIPT_POST_KC" KC_SCRIPT_POST_ENVS

  run_container "${CALDERA_CONTROLLER}" \
    --name "${CALDERA_CONTROLLER}" \
    --hostname "${CALDERA_CONTROLLER}" \
    --restart unless-stopped \
    --network "${CP_NETWORK}" \
    -e "CALDERA_URL=http://${CALDERA_SERVER}:8888" \
    -e "KUBECONFIG=/kube/kubeconfig" \
    -e "ADV_LIST=${ADV_LIST}" \
    "${KC_ENABLE_ENVS[@]}" \
    "${KC_SCRIPT_PRE_ENVS[@]}" \
    "${KC_SCRIPT_POST_ENVS[@]}" \
    -v "./pb/docker/controller/scripts:/scripts:Z" \
    "${IMG_NAME}"
else
  log "Skipping service 'caldera-controller' (env ${svc}_ENABLE != true)"
fi

# -------- attacker --------
svc="attacker"
if is_enabled "$svc"; then
  IMG_NAME="attacker:${IMAGE_VERSION}"
  BUILD_CTX="./src/attacker"
  DOCKERFILE="./src/attacker/Dockerfile"
  if [[ "${BUILD_CONTAINERS_DOCKER}" == "true" ]] || ! image_exists "${IMG_NAME}"; then
    log "Building ${IMG_NAME} from ${BUILD_CTX}"
    docker build -t "${IMG_NAME}" -f "${DOCKERFILE}" "${BUILD_CTX}" \
      --build-arg DOCKER_DAEMON="1" \
      --build-arg CALDERA_URL="http://${CALDERA_SERVER}:8888" \
      --build-arg GROUP="outside" \
      --build-arg ATTACKERADDR="${ATTACKER}"
  else
    log "Image ${IMG_NAME} already present, skipping build."
  fi
  validate_image_or_die "$IMG_NAME"

  # If caldera-server is enabled, wait for it to become healthy before starting attacker
  if is_enabled "caldera-server"; then
    if docker ps -a --format '{{.Names}}' | grep -qx "${CALDERA_SERVER}"; then
      if ! wait_for_health "${CALDERA_SERVER}" 90; then
        warn "Continuing to start attacker even though caldera-server did not become healthy within timeout."
      fi
    else
      warn "caldera-server not found; attacker will be started without waiting."
    fi
  fi

  # Collect all KC-related env vars (as array)
  KC_ENVS=(); collect_env_prefix_into "KC" KC_ENVS
  # Debug (optional): printf '%s\n' "${KC_ENVS[@]}"

  run_container "${ATTACKER}" \
    --name "${ATTACKER}" \
    --hostname "${ATTACKER}" \
    --restart unless-stopped \
    --network "${CP_NETWORK}" \
    --privileged \
    -e "WAIT=0" \
    -e "CALDERA_URL=http://${CALDERA_SERVER}:8888" \
    -e "HOSTREGISTRY=${REGISTRY_NAME}:${REGISTRY_PORT}" \
    -e "ARP_VICTIM=${PROXY}" \
    -e "FRONTEND_PROXY_IP=${FRONTEND_PROXY_IP}" \
    -e "CONTROL_PLANE_NODE=${CONTROL_PLANE_NODE}" \
    -e "CONTROL_PLANE_PORT=${KUBESERVER_PORT}" \
    -e "K8S_IMAGE=${K8S_IMAGE:-registry.k8s.io/kube-apiserver:v1.30.0}" \
    -e "REGISTRY_USER=${REGISTRY_USER}" \
    -e "REGISTRY_PASS=${REGISTRY_PASS}" \
    -e "LOG_NS=${MEM_NAMESPACE}" \
    -e "ATTACKED_NS=${DMZ_NAMESPACE}" \
    -e "AUTH_NS=${APP_NAMESPACE}" \
    -e "ATTACKERADDR=${ATTACKER}" \
    -e "NSPROTO=${APP_NAMESPACE}" \
    -e "NSCREDS=${MEM_NAMESPACE}" \
    -e "NSPAYMT=${PAY_NAMESPACE}" \
    -e "NSDATA=${DAT_NAMESPACE}" \
    "${KC_ENVS[@]}" \
    -v "./pb/docker/attacker/results:/tmp/KCData:Z" \
    "${IMG_NAME}"

  docker cp ./pb/docker/attacker/apiserver "${ATTACKER}:/apiserver"
  docker cp ./pb/docker/attacker/iphost "${ATTACKER}:/tmp/iphost"
else
  log "Skipping service 'attacker' (env ${svc}_ENABLE != true)"
fi

# -------- caldera-server --------
svc="caldera-server"
if is_enabled "$svc"; then
  IMG_NAME="ghcr.io/mitre/caldera:5.2.0"
  CONTAINER_NAME="${CALDERA_SERVER}"
  HOSTNAME="${CALDERA_SERVER}"
  validate_image_or_die "$IMG_NAME"
  run_container "${CONTAINER_NAME}" \
    --name "${CONTAINER_NAME}" \
    --hostname "${HOSTNAME}" \
    --restart unless-stopped \
    --network "${CP_NETWORK}" \
    -v "./pb/docker/caldera/local.yml:/usr/src/app/conf/local.yml:Z" \
    -v "./pb/docker/caldera/adversaries:/usr/src/app/plugins/stockpile/data/adversaries/personal:Z" \
    -v "./pb/docker/caldera/abilities:/usr/src/app/plugins/stockpile/data/abilities/personal:Z" \
    -e "TZ=Europe/Rome" \
    "${IMG_NAME}"
else
  log "Skipping service 'caldera-server' (env ${svc}_ENABLE != true)"
fi

log "All requested services processed."

wait_registry_ready "${REGISTRY_NAME}" "${REGISTRY_PORT}" 300 || exit 1
docker login "${REGISTRY_NAME}:${REGISTRY_PORT}" -u "$REGISTRY_USER" -p "$REGISTRY_PASS"
