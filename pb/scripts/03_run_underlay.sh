#!/usr/bin/env bash
set -euo pipefail

SCRIPTS_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMMON_LIB="${SCRIPTS_ROOT}/lib/common.sh"
if [[ ! -f "${COMMON_LIB}" ]]; then
  printf "[ERROR] Common library not found: %s\n" "${COMMON_LIB}" >&2
  return 1 2>/dev/null || exit 1
fi
source "${COMMON_LIB}"
PROJECT_ROOT="${PROJECT_ROOT:-$(cd "${SCRIPTS_ROOT}/../.." && pwd)}"

# === Defaults (adjust if needed) ===
CP_NETWORK=${CP_NETWORK:-minikube}
BUILD_CONTAINERS_DOCKER=${BUILD_CONTAINERS_DOCKER:-true}
CACHE_IMAGE_REGISTRY=${CACHE_IMAGE_REGISTRY:-true}
REGISTRY_DATA_DIR=${REGISTRY_DATA_DIR:-${PROJECT_ROOT}/pb/docker/registry/data}
DOCKER_BUILD_PARALLELISM=${DOCKER_BUILD_PARALLELISM:-4}
DOCKER_BUILD_RETRY_ATTEMPTS=${DOCKER_BUILD_RETRY_ATTEMPTS:-3}
DOCKER_BUILD_RETRY_DELAY_SECONDS=${DOCKER_BUILD_RETRY_DELAY_SECONDS:-5}
DOCKER_BUILD_TIMEOUT_SECONDS=${DOCKER_BUILD_TIMEOUT_SECONDS:-0}
IMAGE_VERSION=${IMAGE_VERSION:-2.0.2}
REGISTRY_NAME=${REGISTRY_NAME:-registry}
REGISTRY_PORT=${REGISTRY_PORT:-5000}
PROXY=${PROXY:-proxy}
CALDERA_SERVER=${CALDERA_SERVER:-caldera.dock}
CALDERA_CONTROLLER=${CALDERA_CONTROLLER:-caldera.cont}
ATTACKER=${ATTACKER:-caldera.outs}
CONTROL_PLANE_NODE=${CONTROL_PLANE_NODE:-kind-control-plane}
KUBESERVER_PORT=${KUBESERVER_PORT:-6443}
KUBE_CONTEXT="${KUBE_CONTEXT:-}"
REGISTRY_USER="${REGISTRY_USER:?REGISTRY_USER must be set}"
REGISTRY_PASS="${REGISTRY_PASS:?REGISTRY_PASS must be set}"
MEM_NAMESPACE=${MEM_NAMESPACE:-mem}
DMZ_NAMESPACE=${DMZ_NAMESPACE:-dmz}
APP_NAMESPACE=${APP_NAMESPACE:-app}
PAY_NAMESPACE=${PAY_NAMESPACE:-pay}
DAT_NAMESPACE=${DAT_NAMESPACE:-dat}
TST_NAMESPACE=${TST_NAMESPACE:-tst}
FRONTEND_PROXY_IP=${FRONTEND_PROXY_IP:-127.0.0.1}
GENERIC_SVC_PORT=${GENERIC_SVC_PORT:-8085}
GENERIC_SVC_ADDR=${GENERIC_SVC_ADDR:-127.0.0.1}
ADV_LIST=${ADV_LIST:-"KC1 – Image@cluster, KC2 – WiFi@outside, KC3 – FlagATT@outside, KC4 – CRSocket@outside, KC5 – Certificate@outside, KC6 – Etcd@outside"}

normalize_bool_var BUILD_CONTAINERS_DOCKER
normalize_bool_var CACHE_IMAGE_REGISTRY
[[ "${DOCKER_BUILD_PARALLELISM}" =~ ^[0-9]+$ ]] || die "DOCKER_BUILD_PARALLELISM must be an integer >= 1"
(( DOCKER_BUILD_PARALLELISM >= 1 )) || die "DOCKER_BUILD_PARALLELISM must be >= 1"
[[ "${DOCKER_BUILD_RETRY_ATTEMPTS}" =~ ^[0-9]+$ ]] || die "DOCKER_BUILD_RETRY_ATTEMPTS must be an integer >= 1"
(( DOCKER_BUILD_RETRY_ATTEMPTS >= 1 )) || die "DOCKER_BUILD_RETRY_ATTEMPTS must be >= 1"
[[ "${DOCKER_BUILD_RETRY_DELAY_SECONDS}" =~ ^[0-9]+$ ]] || die "DOCKER_BUILD_RETRY_DELAY_SECONDS must be an integer >= 0"
[[ "${DOCKER_BUILD_TIMEOUT_SECONDS}" =~ ^[0-9]+$ ]] || die "DOCKER_BUILD_TIMEOUT_SECONDS must be an integer >= 0"
if (( DOCKER_BUILD_TIMEOUT_SECONDS > 0 )); then
  req timeout
fi
require_port_var REGISTRY_PORT
require_port_var GENERIC_SVC_PORT
[[ -n "${CP_NETWORK}" ]] || die "CP_NETWORK must not be empty."
req docker
req kubectl

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
  if [[ "${!varname+set}" != "set" ]]; then
    return 1
  fi
  case "${!varname,,}" in
    true) return 0 ;;
    false) return 1 ;;
    *) die "Invalid boolean for ${varname}: '${!varname}' (expected true|false)." ;;
  esac
}

kubectl_ctx() {
  if [[ -n "${KUBE_CONTEXT}" ]]; then
    kubectl --context "${KUBE_CONTEXT}" "$@"
  else
    kubectl "$@"
  fi
}

# Check if docker image exists locally
image_exists() {
  local img="$1"
  docker image inspect "$img" >/dev/null 2>&1
}

run_with_docker_build_timeout() {
  if (( DOCKER_BUILD_TIMEOUT_SECONDS <= 0 )); then
    "$@"
    return $?
  fi

  timeout "${DOCKER_BUILD_TIMEOUT_SECONDS}s" "$@"
  return $?
}

ensure_local_image_or_die() {
  local img="$1"
  image_exists "${img}" || die "Required image not found locally: ${img}"
}

build_image_if_needed() {
  local img_name="$1"
  local build_ctx="$2"
  local dockerfile="$3"
  shift 3
  local -a buildargs=( "$@" )

  if is_true "${BUILD_CONTAINERS_DOCKER}" || ! image_exists "${img_name}"; then
    log "Building ${img_name} from ${build_ctx} (dockerfile: ${dockerfile})"
    retry_cmd "${DOCKER_BUILD_RETRY_ATTEMPTS}" "${DOCKER_BUILD_RETRY_DELAY_SECONDS}" "docker build ${img_name}" \
      run_with_docker_build_timeout docker build -t "${img_name}" -f "${dockerfile}" "${buildargs[@]}" "${build_ctx}"
  else
    log "Image ${img_name} already present, skipping build."
  fi
}

build_required_images() {
  local -a pids=()
  local -a labels=()
  local -a failed=()
  local i

  queue_image_build() {
    local label="$1"
    shift

    build_image_if_needed "$@" &
    pids+=("$!")
    labels+=("${label}")

    if (( ${#pids[@]} >= DOCKER_BUILD_PARALLELISM )); then
      local pid="${pids[0]}"
      local done_label="${labels[0]}"
      pids=("${pids[@]:1}")
      labels=("${labels[@]:1}")
      if ! wait "${pid}"; then
        failed+=("${done_label}")
      fi
    fi
  }

  if is_enabled "load-generator"; then
    queue_image_build "load-generator" "load-generator:${IMAGE_VERSION}" "./src/load-generator" "./src/load-generator/Dockerfile"
  fi

  if is_enabled "samba"; then
    queue_image_build "samba" "samba:${IMAGE_VERSION}" "./src/samba" "./src/samba/Dockerfile"
  fi

  if is_enabled "caldera-controller"; then
    queue_image_build "caldera-controller" "caldera-controller:${IMAGE_VERSION}" "./src/caldera-controller" "./src/caldera-controller/Dockerfile"
  fi

  if is_enabled "attacker"; then
    queue_image_build "attacker" "attacker:${IMAGE_VERSION}" "./src/attacker" "./src/attacker/Dockerfile" \
      --build-arg DOCKER_DAEMON="1" \
      --build-arg CALDERA_URL="http://${CALDERA_SERVER}:8888" \
      --build-arg GROUP="outside" \
      --build-arg ATTACKERADDR="${ATTACKER}"
  fi

  for i in "${!pids[@]}"; do
    if ! wait "${pids[$i]}"; then
      failed+=("${labels[$i]}")
    fi
  done

  if (( ${#failed[@]} > 0 )); then
    die "Image build failed for: ${failed[*]}"
  fi
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

copy_into_container_or_die() {
  local src_path="$1"
  local container_name="$2"
  local target_path="$3"

  [[ -e "${src_path}" ]] || die "Required source path not found for docker cp: ${src_path}"
  docker ps -a --format '{{.Names}}' | grep -qx "${container_name}" || die "Container '${container_name}' not found for docker cp."
  docker cp "${src_path}" "${container_name}:${target_path}" || die "Failed to copy '${src_path}' into '${container_name}:${target_path}'."
}

run_caldera_server_service() {
  local svc="caldera-server"
  local img_name="ghcr.io/mitre/caldera:5.2.0"

  if ! is_enabled "$svc"; then
    log "Skipping service 'caldera-server' (env ${svc}_ENABLE != true)"
    return 0
  fi

  validate_image_or_die "$img_name"
  run_container "${CALDERA_SERVER}" \
    --name "${CALDERA_SERVER}" \
    --hostname "${CALDERA_SERVER}" \
    --restart unless-stopped \
    --network "${CP_NETWORK}" \
    -v "./pb/docker/caldera/local.yml:/usr/src/app/conf/local.yml:Z" \
    -v "./pb/docker/caldera/adversaries:/usr/src/app/plugins/stockpile/data/adversaries/personal:Z" \
    -v "./pb/docker/caldera/abilities:/usr/src/app/plugins/stockpile/data/abilities/personal:Z" \
    -e "TZ=Europe/Rome" \
    "${img_name}"
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
    status=$(docker inspect --format '{{.State.Status}}' "$cname" 2>/dev/null || echo "no-health")
    if [ "$status" = "running" ]; then
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
KUBESERVER_PORT="$(kubectl_ctx -n default get endpoints kubernetes -o jsonpath='{.subsets[0].ports[0].port}' 2>/dev/null || echo 6443)"
export KUBESERVER_PORT
K8S_IMAGE="$(kubectl_ctx get pod -n kube-system -l component=kube-apiserver -o jsonpath='{.items[0].spec.containers[0].image}{"\n"}' 2>/dev/null || true)"
export K8S_IMAGE

ensure_network "$CP_NETWORK"
build_required_images

# -------- registry --------
svc="registry"
REG_IMAGE="registry:2"
CONTAINER_NAME="${REGISTRY_NAME}"
HOSTNAME="${REGISTRY_NAME}"
validate_image_or_die "$REG_IMAGE"
registry_storage_mount=()
if is_true "${CACHE_IMAGE_REGISTRY}"; then
  mkdir -p "${REGISTRY_DATA_DIR}"
  registry_storage_mount=( -v "${REGISTRY_DATA_DIR}:/var/lib/registry:Z" )
  log "Registry image cache enabled at '${REGISTRY_DATA_DIR}'."
else
  log "Registry image cache disabled (CACHE_IMAGE_REGISTRY=false)."
fi

run_container "${CONTAINER_NAME}" \
  --name "${CONTAINER_NAME}" \
  --hostname "${HOSTNAME}" \
  --restart always \
  --network "${CP_NETWORK}" \
  -e "REGISTRY_STORAGE_DELETE_ENABLED=true" \
  -e "REGISTRY_HTTP_ADDR=0.0.0.0:${REGISTRY_PORT}" \
  -e "REGISTRY_AUTH=htpasswd" \
  -e "REGISTRY_AUTH_HTPASSWD_REALM=Registry Realm" \
  -e "REGISTRY_AUTH_HTPASSWD_PATH=/auth/htpasswd" \
  -e "REGISTRY_HTTP_TLS_CERTIFICATE=/auth/certs/domain.crt" \
  -e "REGISTRY_HTTP_TLS_KEY=/auth/certs/domain.key" \
  -v "./pb/docker/registry:/auth:Z" \
  "${registry_storage_mount[@]}" \
  "${REG_IMAGE}"

# -------- load-generator --------
svc="load-generator"
if is_enabled "$svc"; then
  IMG_NAME="load-generator:${IMAGE_VERSION}"
  ensure_local_image_or_die "${IMG_NAME}"
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
  ensure_local_image_or_die "${IMG_NAME}"
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
    -p "0.0.0.0:${GENERIC_SVC_PORT}:${GENERIC_SVC_PORT}" \
    -e "CALDERA_SERVER=${CALDERA_SERVER}" \
    -e "FRONTEND_PROXY=${FRONTEND_PROXY_IP}" \
    -e "GENERIC_SVC_PORT=${GENERIC_SVC_PORT}" \
    -e "GENERIC_SVC_ADDR=${GENERIC_SVC_ADDR}" \
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
  ensure_local_image_or_die "${IMG_NAME}"
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
    -e "DOCKER_HOST=unix:///var/run/docker.sock" \
    -e "ATT_OUT=${ATTACKER}" \
    -e "ATT_NS=${TST_NAMESPACE}" \
    -v "/var/run/docker.sock:/var/run/docker.sock" \
    "${KC_ENABLE_ENVS[@]}" \
    "${KC_SCRIPT_PRE_ENVS[@]}" \
    "${KC_SCRIPT_POST_ENVS[@]}" \
    -v "./pb/docker/controller/scripts:/scripts:Z" \
    "${IMG_NAME}"
else
  log "Skipping service 'caldera-controller' (env ${svc}_ENABLE != true)"
fi

# -------- attacker --------
run_caldera_server_service

svc="attacker"
if is_enabled "$svc"; then
  IMG_NAME="attacker:${IMAGE_VERSION}"
  ensure_local_image_or_die "${IMG_NAME}"
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
    "${IMG_NAME}"

  copy_into_container_or_die "./pb/docker/attacker/apiserver" "${ATTACKER}" "/apiserver"
  copy_into_container_or_die "./pb/docker/attacker/iphost" "${ATTACKER}" "/tmp/iphost"
else
  log "Skipping service 'attacker' (env ${svc}_ENABLE != true)"
fi

log "All requested services processed."
