#!/usr/bin/env bash
set -euo pipefail

SCRIPTS_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMMON_LIB="${SCRIPTS_ROOT}/lib/common.sh"
if [[ ! -f "${COMMON_LIB}" ]]; then
  printf "[ERROR] Common library not found: %s\n" "${COMMON_LIB}" >&2
  return 1 2>/dev/null || exit 1
fi
source "${COMMON_LIB}"
PROJECT_ROOT="$(cd "${SCRIPTS_ROOT}/../.." && pwd)"
RESOURCES_ROOT="${SCRIPTS_ROOT}/res"
ADDITIONS_OVERRIDES_TEMPLATE="${RESOURCES_ROOT}/additions_overrides.yaml.tpl"
ASTRONOMY_OVERRIDES_TEMPLATE="${RESOURCES_ROOT}/astronomy_overrides.yaml.tpl"
TELEMETRY_OVERRIDES_TEMPLATE="${RESOURCES_ROOT}/telemetry_overrides.yaml.tpl"
DOCKERFILE_BASE_IMAGES_FILE="${DOCKERFILE_BASE_IMAGES_FILE:-${RESOURCES_ROOT}/dockerfile_base_images.list}"

# =========================================
# Parameters (env overridable)
# =========================================
IMAGE_VERSION="${IMAGE_VERSION:-2.0.2}"

# Registry the HELPER will push to (must match the name used in your image tags)
REGISTRY_NAME="${REGISTRY_NAME:-registry}"
REGISTRY_PORT="${REGISTRY_PORT:-5000}"

# Start an internal registry inside the helper (HTTP by default)
INTERNAL_REGISTRY="${INTERNAL_REGISTRY:-false}"

# If your registry is HTTP, set this true so the helper's dockerd treats it as insecure
INSECURE_REGISTRY="${INSECURE_REGISTRY:-false}"

# Optional login (for authenticated registries)
REGISTRY_USER="${REGISTRY_USER:-}"
REGISTRY_PASS="${REGISTRY_PASS:-}"

# Host paths to your registry certificates
HOST_REGISTRY_CA="${HOST_REGISTRY_CA:-./pb/docker/registry/certs/rootca.crt}"
HOST_REGISTRY_CERT="${HOST_REGISTRY_CERT:-./pb/docker/registry/certs/domain.crt}"  # optional

APP_NAMESPACE="${APP_NAMESPACE:-app}"
DAT_NAMESPACE="${DAT_NAMESPACE:-dat}"
DMZ_NAMESPACE="${DMZ_NAMESPACE:-dmz}"
MEM_NAMESPACE="${MEM_NAMESPACE:-mem}"
PAY_NAMESPACE="${PAY_NAMESPACE:-pay}"
TST_NAMESPACE="${TST_NAMESPACE:-tst}"

# Build policy: true = force rebuild and push; false = build only if missing from registry (or push if only local)
BUILD_CONTAINERS_K8S="${BUILD_CONTAINERS_K8S:-false}"

# Number of concurrent image build/push jobs (1 = sequential)
DOCKER_BUILD_PARALLELISM="${DOCKER_BUILD_PARALLELISM:-4}"
DOCKER_BUILD_RETRY_ATTEMPTS="${DOCKER_BUILD_RETRY_ATTEMPTS:-3}"
DOCKER_BUILD_RETRY_DELAY_SECONDS="${DOCKER_BUILD_RETRY_DELAY_SECONDS:-5}"
DOCKER_BUILD_TIMEOUT_SECONDS="${DOCKER_BUILD_TIMEOUT_SECONDS:-0}"

# Optional multi-arch build (e.g., "linux/amd64,linux/arm64")
PLATFORM="${PLATFORM:-}"

# Helm
HELM_TIMEOUT="${HELM_TIMEOUT:-20m}"

# Docker helper (dedicated daemon/socket via docker:dind)
DOCKER_HELPER_IMAGE="${DOCKER_HELPER_IMAGE:-docker:26.1-dind}"
DOCKER_HELPER_NAME="${DOCKER_HELPER_NAME:-docker-cli-helper}"
DOCKER_HELPER_DATA_DIR="${PROJECT_ROOT}/pb/docker/helper"
ENABLE_HELPER_CACHE="${ENABLE_HELPER_CACHE:-true}"
PREFETCH_DOCKERFILE_BASE_IMAGES="${PREFETCH_DOCKERFILE_BASE_IMAGES:-true}"
WORKDIR="${PROJECT_ROOT}"

# Network the helper container will join
CP_NETWORK="${CP_NETWORK:-bridge}"
# API server IPs injected into NetworkPolicies (YAML list or "auto")
KUBE_APISERVER_IPS="${KUBE_APISERVER_IPS:-auto}"
# Deprecated fallback (host-only /32 or /128 entries)
KUBE_APISERVER_CIDRS="${KUBE_APISERVER_CIDRS:-}"

normalize_bool_var INTERNAL_REGISTRY
normalize_bool_var INSECURE_REGISTRY
normalize_bool_var BUILD_CONTAINERS_K8S
normalize_bool_var ENABLE_HELPER_CACHE
normalize_bool_var PREFETCH_DOCKERFILE_BASE_IMAGES
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
[[ -n "${REGISTRY_NAME}" ]] || die "REGISTRY_NAME must not be empty."
[[ -n "${DOCKER_HELPER_NAME}" ]] || die "DOCKER_HELPER_NAME must not be empty."

# Requirements
req docker
req helm
req kubectl
cd "${PROJECT_ROOT}"

# =========================================
# Docker helper lifecycle
# =========================================
start_docker_helper() {
  docker rm -f "${DOCKER_HELPER_NAME}" >/dev/null 2>&1 || true

  log "Starting Docker helper '${DOCKER_HELPER_NAME}' with ${DOCKER_HELPER_IMAGE}"
  local -a helper_storage_mount=()
  local dind_args=()
  local helper_ready=false

  if is_true "${ENABLE_HELPER_CACHE}"; then
    mkdir -p "${DOCKER_HELPER_DATA_DIR}"
    helper_storage_mount=( -v "${DOCKER_HELPER_DATA_DIR}:/var/lib/docker" )
    log "Docker helper cache enabled at '${DOCKER_HELPER_DATA_DIR}'."
  else
    log "Docker helper cache disabled (ENABLE_HELPER_CACHE=false)."
  fi

  if is_true "${INSECURE_REGISTRY}"; then
    dind_args+=( "--insecure-registry=${REGISTRY_NAME}:${REGISTRY_PORT}" )
  fi

  if ! retry_cmd "${DOCKER_BUILD_RETRY_ATTEMPTS}" "${DOCKER_BUILD_RETRY_DELAY_SECONDS}" "docker run ${DOCKER_HELPER_NAME}" \
    docker run -d --rm --name "${DOCKER_HELPER_NAME}" \
      --privileged \
      --network "${CP_NETWORK}" \
      -v "${WORKDIR}:${WORKDIR}:ro" \
      "${helper_storage_mount[@]}" \
      "${DOCKER_HELPER_IMAGE}" \
      "${dind_args[@]}" >/dev/null; then
    return 1
  fi

  # Wait for helper's Docker daemon
  log "Waiting for helper's Docker daemon..."
  for i in {1..60}; do
    if docker exec "${DOCKER_HELPER_NAME}" docker info >/dev/null 2>&1; then
      helper_ready=true
      break
    fi
    sleep 1
  done

  if ! is_true "${helper_ready}"; then
    die "Helper Docker daemon did not become ready in time"
    return 1
  fi

  # Install registry CA so HTTPS pushes succeed
  if ! install_registry_ca; then
    return 1
  fi

  # Optionally start an internal registry (HTTP by default)
  if is_true "${INTERNAL_REGISTRY}"; then
    log "Ensuring internal registry (${REGISTRY_NAME}:${REGISTRY_PORT}) is running inside helper..."
    if ! docker exec "${DOCKER_HELPER_NAME}" docker ps --format '{{.Names}}' | grep -q "^${REGISTRY_NAME}\$"; then
      docker exec "${DOCKER_HELPER_NAME}" docker run -d --name "${REGISTRY_NAME}" \
        -p "${REGISTRY_PORT}:5000" \
        --restart=always registry:2 >/dev/null
      if ! is_true "${INSECURE_REGISTRY}"; then
        warn "Internal registry started without TLS; set INSECURE_REGISTRY=true for HTTP pushes."
      fi
    fi
  fi

  # Optional login (internal registry is unauthenticated by default)
  if [[ -n "${REGISTRY_USER}" && -n "${REGISTRY_PASS}" ]]; then
    log "Logging into ${REGISTRY_NAME}:${REGISTRY_PORT} from helper"
    if ! printf '%s\n' "${REGISTRY_PASS}" | docker exec -i "${DOCKER_HELPER_NAME}" \
         docker login "${REGISTRY_NAME}:${REGISTRY_PORT}" --username "${REGISTRY_USER}" --password-stdin >/dev/null; then
      warn "Login failed (registry may be unauthenticated) – continuing."
    fi
  else
    warn "REGISTRY_USER/REGISTRY_PASS not set; skipping 'docker login'."
  fi
}

stop_docker_helper() {
  if is_true "${DOCKER_HELPER_STOPPED:-false}"; then
    return 0
  fi
  log "Stopping Docker helper '${DOCKER_HELPER_NAME}'"
  docker rm -f "${DOCKER_HELPER_NAME}" >/dev/null 2>&1 || true
  DOCKER_HELPER_STOPPED=true
}

# Execute Docker CLI inside helper
d() { docker exec "${DOCKER_HELPER_NAME}" docker "$@"; }

run_helper_docker_build_with_timeout() {
  if (( DOCKER_BUILD_TIMEOUT_SECONDS <= 0 )); then
    docker exec "${DOCKER_HELPER_NAME}" docker "$@"
    return $?
  fi

  timeout "${DOCKER_BUILD_TIMEOUT_SECONDS}s" \
    docker exec "${DOCKER_HELPER_NAME}" sh -lc '
      timeout_s="$1"
      shift
      timeout -s TERM -k 10 "${timeout_s}" docker "$@"
    ' _ "${DOCKER_BUILD_TIMEOUT_SECONDS}" "$@"
  return $?
}

run_with_docker_build_timeout() {
  if (( DOCKER_BUILD_TIMEOUT_SECONDS <= 0 )); then
    "$@"
    return $?
  fi

  timeout "${DOCKER_BUILD_TIMEOUT_SECONDS}s" "$@"
  return $?
}

# Normalized boolean helpers for env-driven Helm values.
bool_env_value() {
  local var_name="$1"
  local default_value="$2"
  local value="${!var_name:-$default_value}"

  case "${value,,}" in
    true|false) printf '%s' "${value,,}" ;;
    *) die "Invalid boolean for ${var_name}: '${value}' (expected true|false)." ;;
  esac
}

helm_bool() {
  local var_name="$1"
  local default_value="$2"
  if is_true "$(bool_env_value "$var_name" "$default_value")"; then
    printf 'true'
  else
    printf 'false'
  fi
}

helm_bool_all() {
  local var_a="$1"
  local def_a="$2"
  local var_b="$3"
  local def_b="$4"
  if is_true "$(bool_env_value "$var_a" "$def_a")" && is_true "$(bool_env_value "$var_b" "$def_b")"; then
    printf 'true'
  else
    printf 'false'
  fi
}

bool_text() {
  local var_name="$1"
  local default_value="$2"
  local on_true="$3"
  local on_false="$4"
  if is_true "$(bool_env_value "$var_name" "$default_value")"; then
    printf '%s' "$on_true"
  else
    printf '%s' "$on_false"
  fi
}

render_values_template() {
  local template_path="$1"
  local output_path="$2"
  local template_body=""

  [[ -f "${template_path}" ]] || die "Template file not found: ${template_path}"
  template_body="$(< "${template_path}")"

  eval "cat <<__HONEY_TEMPLATE__
${template_body}
__HONEY_TEMPLATE__" > "${output_path}"
}

is_ipv4_literal() {
  [[ "$1" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]]
}

is_ipv6_literal() {
  [[ "$1" == *:* ]]
}

is_loopback_ip() {
  [[ "$1" == "127.0.0.1" || "$1" == "::1" ]]
}

normalize_legacy_kube_apiserver_cidrs() {
  local legacy_payload="$1"
  local stripped="${legacy_payload#[}"
  stripped="${stripped%]}"
  local -a raw_entries=()
  local -a normalized_entries=()
  local -A seen=()
  local entry=""
  local prefix=""

  if [[ -z "${stripped//[[:space:]]/}" ]]; then
    printf '[]'
    return 0
  fi

  IFS=',' read -r -a raw_entries <<< "${stripped}"
  for entry in "${raw_entries[@]}"; do
    entry="$(sed -E 's/^[[:space:]]*"*//;s/"*[[:space:]]*$//' <<< "${entry}")"
    [[ -n "${entry}" ]] || continue

    if [[ "${entry}" == */* ]]; then
      prefix="${entry##*/}"
      if [[ "${prefix}" != "32" && "${prefix}" != "128" ]]; then
        die "KUBE_APISERVER_CIDRS supports only host CIDRs (/32 or /128). Invalid entry: ${entry}"
      fi
      entry="${entry%/*}"
    fi

    if ! is_ipv4_literal "${entry}" && ! is_ipv6_literal "${entry}"; then
      die "Invalid API server IP in KUBE_APISERVER_CIDRS: ${entry}"
    fi
    if is_loopback_ip "${entry}"; then
      continue
    fi
    if [[ -n "${seen[${entry}]:-}" ]]; then
      continue
    fi

    seen["${entry}"]=1
    normalized_entries+=("\"${entry}\"")
  done

  if (( ${#normalized_entries[@]} == 0 )); then
    printf '[]'
    return 0
  fi

  printf '[%s]' "$(IFS=,; echo "${normalized_entries[*]}")"
}

discover_kube_apiserver_ips() {
  local -a raw_ips=()
  local -a unique_ips=()
  local -a ip_list=()
  local -A seen=()
  local ip=""
  local host=""
  local host_only=""
  local server_url=""
  local ip_payload=""

  if [[ "${KUBE_APISERVER_IPS}" != "auto" ]]; then
    log "Using user-provided KUBE_APISERVER_IPS=${KUBE_APISERVER_IPS}"
    return 0
  fi

  if [[ -n "${KUBE_APISERVER_CIDRS}" && "${KUBE_APISERVER_CIDRS}" != "auto" ]]; then
    warn "KUBE_APISERVER_CIDRS is deprecated. Converting to host IP list for API server-only policy."
    KUBE_APISERVER_IPS="$(normalize_legacy_kube_apiserver_cidrs "${KUBE_APISERVER_CIDRS}")"
    export KUBE_APISERVER_IPS
    log "Using API server IPs from KUBE_APISERVER_CIDRS: ${KUBE_APISERVER_IPS}"
    return 0
  fi

  while IFS= read -r ip; do
    [[ -n "${ip}" ]] && raw_ips+=("${ip}")
  done < <(
    kubectl get endpoints -n default kubernetes \
      -o jsonpath='{range .subsets[*].addresses[*]}{.ip}{"\n"}{end}' 2>/dev/null || true
  )

  while IFS= read -r ip; do
    [[ -n "${ip}" && "${ip}" != "None" ]] && raw_ips+=("${ip}")
  done < <(
    kubectl get svc -n default kubernetes \
      -o jsonpath='{.spec.clusterIP}{"\n"}{range .spec.clusterIPs[*]}{.}{"\n"}{end}' 2>/dev/null || true
  )

  server_url="$(kubectl config view --raw --minify -o jsonpath='{.clusters[0].cluster.server}' 2>/dev/null || true)"
  if [[ -n "${server_url}" ]]; then
    host="${server_url#*://}"
    host="${host%%/*}"

    if [[ "${host}" == \[* ]]; then
      host_only="${host#\[}"
      host_only="${host_only%%]*}"
    else
      host_only="${host%%:*}"
    fi

    if [[ -n "${host_only}" ]]; then
      if is_ipv4_literal "${host_only}" || is_ipv6_literal "${host_only}"; then
        raw_ips+=("${host_only}")
      elif command -v getent >/dev/null 2>&1; then
        while IFS= read -r ip; do
          [[ -n "${ip}" ]] && raw_ips+=("${ip}")
        done < <(getent ahosts "${host_only}" 2>/dev/null | awk '{print $1}' | awk 'NF' | sort -u)
      fi
    fi
  fi

  for ip in "${raw_ips[@]}"; do
    if ! is_ipv4_literal "${ip}" && ! is_ipv6_literal "${ip}"; then
      continue
    fi
    if is_loopback_ip "${ip}"; then
      continue
    fi
    if [[ -n "${seen[${ip}]:-}" ]]; then
      continue
    fi
    seen["${ip}"]=1
    unique_ips+=("${ip}")
  done

  if (( ${#unique_ips[@]} == 0 )); then
    warn "Could not discover API server IPs; KUBE_APISERVER_IPS set to []."
    KUBE_APISERVER_IPS='[]'
    export KUBE_APISERVER_IPS
    return 0
  fi

  for ip in "${unique_ips[@]}"; do
    ip_list+=("\"${ip}\"")
  done

  ip_payload="$(IFS=,; echo "${ip_list[*]}")"
  KUBE_APISERVER_IPS="[${ip_payload}]"
  export KUBE_APISERVER_IPS
  log "Discovered API server IPs: ${KUBE_APISERVER_IPS}"
}

# =========================================
# Certificates install
# =========================================
install_registry_ca() {
  local helper="${DOCKER_HELPER_NAME}"
  local reg="${REGISTRY_NAME}:${REGISTRY_PORT}"

  if [[ ! -f "${HOST_REGISTRY_CA}" ]]; then
    warn "HOST_REGISTRY_CA not found at '${HOST_REGISTRY_CA}' — skipping CA install."
    return 0
  fi

  log "Installing registry CA into helper trust store for ${reg}"
  docker exec "${helper}" sh -lc "mkdir -p /etc/docker/certs.d/${reg}"
  docker cp "${HOST_REGISTRY_CA}" "${helper}:/etc/docker/certs.d/${reg}/ca.crt"
  if [[ -f "${HOST_REGISTRY_CERT}" ]]; then
    docker cp "${HOST_REGISTRY_CERT}" "${helper}:/etc/docker/certs.d/${reg}/domain.crt" || true
  fi
  docker exec "${helper}" sh -lc 'kill -SIGHUP 1 || true'
}

# =========================================
# Image helpers
# =========================================
tag_for(){ echo "${REGISTRY_NAME}:${REGISTRY_PORT}/$1:${IMAGE_VERSION}"; }

remote_image_exists(){ d manifest inspect "$1" >/dev/null 2>&1; }
local_image_exists(){  d image inspect    "$1" >/dev/null 2>&1; }

helper_abs_path() {
  local maybe_rel="$1"
  if [[ "${maybe_rel}" = /* ]]; then
    printf '%s' "${maybe_rel}"
  else
    printf '%s/%s' "${PROJECT_ROOT}" "${maybe_rel#./}"
  fi
}

prefetch_dockerfile_base_images() {
  local list_file="${DOCKERFILE_BASE_IMAGES_FILE}"
  local line=""
  local image=""
  local pulled=0
  local skipped=0
  local failed=0

  if ! is_true "${PREFETCH_DOCKERFILE_BASE_IMAGES}"; then
    log "Skipping Dockerfile base image pre-pull (PREFETCH_DOCKERFILE_BASE_IMAGES=false)."
    return 0
  fi

  if [[ ! -f "${list_file}" ]]; then
    warn "Dockerfile base images list not found at '${list_file}' - skipping pre-pull."
    return 0
  fi

  log "Pre-pulling Dockerfile base images from '${list_file}'"
  while IFS= read -r line || [[ -n "${line}" ]]; do
    image="$(sed -E 's/[[:space:]]*#.*$//;s/^[[:space:]]+//;s/[[:space:]]+$//' <<< "${line}")"
    [[ -n "${image}" ]] || continue

    if [[ "${image}" == "${REGISTRY_NAME}:${REGISTRY_PORT}/"* ]]; then
      skipped=$((skipped + 1))
      log "Skipping pre-pull for project image '${image}' (same registry target)."
      continue
    fi

    if retry_cmd "${DOCKER_BUILD_RETRY_ATTEMPTS}" "${DOCKER_BUILD_RETRY_DELAY_SECONDS}" "docker pull ${image}" \
      d pull "${image}"; then
      pulled=$((pulled + 1))
    else
      failed=$((failed + 1))
      warn "Could not pre-pull '${image}' in helper. The build may pull it on-demand."
    fi
  done < "${list_file}"

  if (( failed > 0 )); then
    warn "Dockerfile base image pre-pull finished with errors (pulled=${pulled}, failed=${failed}, skipped=${skipped})."
  else
    log "Dockerfile base image pre-pull completed (pulled=${pulled}, failed=0, skipped=${skipped})."
  fi
}

ensure_helper_builder() {
  if [[ "${HELPER_BUILDER_READY:-false}" == "true" ]]; then
    return 0
  fi
  d buildx inspect honeypotbx >/dev/null 2>&1 || d buildx create --name honeypotbx >/dev/null 2>&1
  d buildx use honeypotbx >/dev/null 2>&1 || true
  HELPER_BUILDER_READY=true
}

helper_build_and_push(){
  local tag="$1" ctx="$2" df="$3"; shift 3
  local -a buildargs=( "$@" )
  local abs_ctx abs_df

  abs_ctx="$(helper_abs_path "$ctx")"
  abs_df="$(helper_abs_path "$df")"

  [[ -d "${abs_ctx}" ]] || die "Build context does not exist: ${abs_ctx}"
  [[ -f "${abs_df}" ]] || die "Dockerfile does not exist: ${abs_df}"

  log "Building in helper -> ${tag} (context=${abs_ctx}, dockerfile=${abs_df})"
  if [[ -n "${PLATFORM}" ]]; then
    ensure_helper_builder
    retry_cmd "${DOCKER_BUILD_RETRY_ATTEMPTS}" "${DOCKER_BUILD_RETRY_DELAY_SECONDS}" "buildx+push ${tag}" \
      run_helper_docker_build_with_timeout buildx build --platform "${PLATFORM}" -t "${tag}" -f "${abs_df}" --network "host" "${buildargs[@]}" "${abs_ctx}" --push
  else
    if ! retry_cmd "${DOCKER_BUILD_RETRY_ATTEMPTS}" "${DOCKER_BUILD_RETRY_DELAY_SECONDS}" "docker build ${tag}" \
      run_helper_docker_build_with_timeout build -t "${tag}" -f "${abs_df}" --network "host" "${buildargs[@]}" "${abs_ctx}"; then
      err "docker build failed for ${tag}; skipping push."
      return 1
    fi
    retry_cmd "${DOCKER_BUILD_RETRY_ATTEMPTS}" "${DOCKER_BUILD_RETRY_DELAY_SECONDS}" "docker push ${tag}" \
      d push "${tag}"
  fi
}

ensure_image(){
  local name="$1" ctx="$2" df="$3"; shift 3
  local -a buildargs=( "$@" )
  local tag; tag="$(tag_for "$name")"

  # Force rebuild/push
  if is_true "$BUILD_CONTAINERS_K8S"; then
    helper_build_and_push "$tag" "$ctx" "$df" "${buildargs[@]}"; return
  fi

  # Already in remote registry?
  if remote_image_exists "$tag"; then
    log "Skipping build ($name): already in registry -> $tag"; return
  fi

  # Present in helper's daemon?
  if local_image_exists "$tag"; then
    log "Pushing only (found locally in helper, missing remotely): $tag"
    retry_cmd "${DOCKER_BUILD_RETRY_ATTEMPTS}" "${DOCKER_BUILD_RETRY_DELAY_SECONDS}" "docker push ${tag}" \
      d push "$tag"
    return
  fi

  # Otherwise, build and push directly from helper daemon.
  helper_build_and_push "$tag" "$ctx" "$df" "${buildargs[@]}"
}

# =========================================
# Build/push images
# =========================================
build_all_images(){
  local -a pids=()
  local -a labels=()
  local -a failed=()
  local i

  queue_image_build() {
    local label="$1"
    shift

    ensure_image "$@" &
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

  if [[ -n "${PLATFORM}" ]]; then
    # Avoid races in parallel jobs when creating/selecting the buildx builder.
    ensure_helper_builder
  fi

  queue_image_build accounting accounting src/accounting src/accounting/Dockerfile
  queue_image_build ad ad src/ad src/ad/Dockerfile

  queue_image_build attacker attacker src/attacker src/attacker/Dockerfile \
    --build-arg GROUP="cluster" \
    --build-arg LOG_NS="${MEM_NAMESPACE}" \
    --build-arg ATTACKED_NS="${DMZ_NAMESPACE}" \
    --build-arg AUTH_NS="${APP_NAMESPACE}" \
    --build-arg ATTACKERADDR="${ATTACKER:-}" \
    --build-arg CALDERA_URL="http://${CALDERA_SERVER:-}:8888" \
    --build-arg KC0101="${KC0101:-}" \
    --build-arg KC0102="${KC0102:-}" \
    --build-arg KC0103="${KC0103:-}" \
    --build-arg KC0104="${KC0104:-}" \
    --build-arg KC0105="${KC0105:-}" \
    --build-arg KC0106="${KC0106:-}" \
    --build-arg KC0107="${KC0107:-}" \
    --build-arg KC0108="${KC0108:-}"

  queue_image_build auth auth src/auth src/auth/Dockerfile
  queue_image_build cart cart src/cart src/cart/src/Dockerfile
  queue_image_build checkout checkout src/checkout src/checkout/Dockerfile
  queue_image_build controller controller src/controller src/controller/Dockerfile
  queue_image_build currency currency src/currency src/currency/Dockerfile
  queue_image_build email email src/email src/email/Dockerfile
  queue_image_build flagd flagd src/flagd src/flagd/Dockerfile
  queue_image_build flagd-ui flagd-ui src/flagd-ui src/flagd-ui/Dockerfile
  queue_image_build fraud-detection fraud-detection src/fraud-detection src/fraud-detection/Dockerfile
  queue_image_build frontend frontend src/frontend src/frontend/Dockerfile
  queue_image_build frontend-proxy frontend-proxy src/frontend-proxy src/frontend-proxy/Dockerfile
  queue_image_build image-provider image-provider src/image-provider src/image-provider/Dockerfile
  queue_image_build kafka kafka src/kafka src/kafka/Dockerfile
  queue_image_build payment payment src/payment src/payment/Dockerfile

  queue_image_build postgres postgres src/postgres src/postgres/Dockerfile --build-arg DB="curr"
  queue_image_build postgres-auth postgres-auth src/postgres src/postgres/Dockerfile --build-arg DB="auth"
  queue_image_build postgres-payment postgres-payment src/postgres src/postgres/Dockerfile --build-arg DB="pay"

  queue_image_build product-catalog product-catalog src/product-catalog src/product-catalog/Dockerfile
  queue_image_build quote quote src/quote src/quote/Dockerfile
  queue_image_build recommendation recommendation src/recommendation src/recommendation/Dockerfile
  queue_image_build shipping shipping src/shipping src/shipping/Dockerfile
  queue_image_build sidecar-enc sidecar-enc src/sidecar-enc src/sidecar-enc/Dockerfile
  queue_image_build sidecar-mal sidecar-mal src/sidecar-mal src/sidecar-mal/Dockerfile
  queue_image_build smtp smtp src/smtp src/smtp/Dockerfile
  queue_image_build valkey-cart valkey-cart src/valkey-cart src/valkey-cart/Dockerfile
  queue_image_build traffic-translator traffic-translator src/traffic-translator src/traffic-translator/Dockerfile

  for i in "${!pids[@]}"; do
    if ! wait "${pids[$i]}"; then
      failed+=("${labels[$i]}")
    fi
  done

  if (( ${#failed[@]} > 0 )); then
    err "Image build/push failed for: ${failed[*]}"
    return 1
  fi
}

# =========================================
# Helm Deploy
# =========================================
helm_repos(){
  helm repo add metallb https://metallb.github.io/metallb >/dev/null 2>&1 || true
  helm repo add csi-driver-smb https://raw.githubusercontent.com/kubernetes-csi/csi-driver-smb/master/charts >/dev/null 2>&1 || true
  helm repo update >/dev/null
}

deploy_helm(){
  helm_repos
  discover_kube_apiserver_ips

  # metallb
  helm upgrade --install metallb metallb/metallb \
    --namespace metallb-system --create-namespace \
    --wait --timeout "${HELM_TIMEOUT}" || return $?

  # csi-driver-smb
  if [ "${SAMBA_ENABLE:-true}" = "true" ]; then
    helm upgrade --install csi-driver-smb csi-driver-smb/csi-driver-smb \
      --namespace kube-system \
      --wait --timeout "${HELM_TIMEOUT}" || return $?
  fi

  # honeypot-additions
  local additions_overrides
  additions_overrides="$(mktemp)"
  trap_add "rm -f '${additions_overrides}'" EXIT
  render_values_template "${ADDITIONS_OVERRIDES_TEMPLATE}" "${additions_overrides}"

  helm upgrade --install honeypot-additions helm-charts/additions \
    --wait --timeout "${HELM_TIMEOUT}" \
    -f helm-charts/additions/values.yaml \
    -f "${additions_overrides}" || return $?

  # honeypot-telemetry
  local TELEMETRY_VALUES telemetry_overrides
  TELEMETRY_VALUES="helm-charts/telemetry/values-$(bool_text LOG_OPEN true noauth auth).yaml"
  telemetry_overrides="$(mktemp)"
  trap_add "rm -f '${telemetry_overrides}'" EXIT
  render_values_template "${TELEMETRY_OVERRIDES_TEMPLATE}" "${telemetry_overrides}"

  helm upgrade --install honeypot-telemetry helm-charts/telemetry \
    --namespace "${MEM_NAMESPACE}" --create-namespace \
    --wait --timeout "${HELM_TIMEOUT}" \
    -f "${TELEMETRY_VALUES}" \
    -f "${telemetry_overrides}" || return $?

  local astronomy_overrides
  astronomy_overrides="$(mktemp)"
  trap_add "rm -f '${astronomy_overrides}'" EXIT
  render_values_template "${ASTRONOMY_OVERRIDES_TEMPLATE}" "${astronomy_overrides}"

  # honeypot-astronomy-shop
  helm upgrade --install honeypot-astronomy-shop helm-charts/astronomy-shop \
    --wait --timeout "${HELM_TIMEOUT}" \
    -f helm-charts/astronomy-shop/values.yaml \
    -f "${astronomy_overrides}" \
    --set "components.auth.initContainers[0].env[0].value=postgres-auth.${DAT_NAMESPACE}" \
    --set "components.cart.initContainers[0].env[0].value=valkey-cart.${DAT_NAMESPACE}" \
    --set "components.checkout.initContainers[1].env[0].value=flagd.${MEM_NAMESPACE}" \
    --set "components.checkout.sidecarContainers[0].imageOverride.repository=${REGISTRY_NAME}:${REGISTRY_PORT}/sidecar-enc" \
    --set "components.checkout.sidecarContainers[0].env[3].value=flagd.${MEM_NAMESPACE}" \
    --set "components.checkout.sidecarContainers[1].imageOverride.repository=${REGISTRY_NAME}:${REGISTRY_PORT}/sidecar-enc" \
    --set "components.checkout.sidecarContainers[1].env[2].value=payment.${PAY_NAMESPACE}:8080" \
    --set "components.checkout.sidecarContainers[1].env[3].value=flagd.${MEM_NAMESPACE}" \
    --set "components.currency.initContainers[0].env[0].value=postgres.${DAT_NAMESPACE}" \
    --set "components.frontend.initContainers[0].env[0].value=flagd.${MEM_NAMESPACE}" \
    --set "components.frontend.sidecarContainers[0].imageOverride.repository=${REGISTRY_NAME}:${REGISTRY_PORT}/sidecar-enc" \
    --set "components.frontend.sidecarContainers[0].env[3].value=flagd.${MEM_NAMESPACE}" \
    --set "components.payment.initContainers[0].env[0].value=flagd.${MEM_NAMESPACE}" \
    --set "components.payment.initContainers[1].env[0].value=postgres-payment.${DAT_NAMESPACE}" \
    --set "components.payment.sidecarContainers[0].imageOverride.repository=${REGISTRY_NAME}:${REGISTRY_PORT}/sidecar-enc" \
    --set "components.payment.sidecarContainers[0].env[3].value=flagd.${MEM_NAMESPACE}" \
    --set "components.traffic-controller.sidecarContainers[0].imageOverride.repository=${REGISTRY_NAME}:${REGISTRY_PORT}/traffic-translator" \
    --set "components.smtp.additionalVolumes[0].hostPath.path=$(bool_text SOCKET_SHARED true "$CRICTL_RUNTIME_PATH" /tmp/disabled-containerd.sock)" \
    --set "components.smtp.additionalVolumes[0].hostPath.type=$(bool_text SOCKET_SHARED true Socket FileOrCreate)" \
    --set "components.smtp.volumeMounts[0].mountPath=/host/run/containerd/containerd.sock" \
    --set "components.checkout.sidecarContainers[0].env[7].value=$(helm_bool FLAGD_FEATURES true)" \
    --set "components.checkout.sidecarContainers[1].env[7].value=$(helm_bool FLAGD_FEATURES true)" \
    --set "components.frontend.sidecarContainers[0].env[7].value=$(helm_bool FLAGD_FEATURES true)" \
    --set "components.payment.sidecarContainers[0].env[7].value=$(helm_bool FLAGD_FEATURES true)" \
    --set "components.flagd.sidecarContainers[0].envSwitchFrom[0].valueFrom=$(bool_text FLAGD_CONFIGMAP true configmap secret)" \
    --set "components.flagd.sidecarContainers[0].envSwitchFrom[1].valueFrom=$(bool_text FLAGD_CONFIGMAP true configmap secret)" || return $?
}

# =========================================
# Main
# =========================================
main() {
  local rc=0

  trap_add stop_docker_helper EXIT

  if ! start_docker_helper; then
    return 1
  fi

  if ! prefetch_dockerfile_base_images; then
    return 1
  fi

  log "== Docker images =="
  if ! build_all_images; then
    rc=1
    err "Docker build/push phase failed."
  fi

  if (( rc == 0 )); then
    log "== Helm Deploy =="
    if ! deploy_helm; then
      rc=1
      err "Helm deploy phase failed."
    fi
  else
    warn "Skipping Helm deploy because Docker build/push failed."
  fi

  if (( rc != 0 )); then
    return "${rc}"
  fi

  log "Done. Registry=${REGISTRY_NAME}:${REGISTRY_PORT} | Version=${IMAGE_VERSION} | BUILD_CONTAINERS_K8S=${BUILD_CONTAINERS_K8S} | INTERNAL_REGISTRY=${INTERNAL_REGISTRY} | INSECURE_REGISTRY=${INSECURE_REGISTRY}"
  return 0
}

main
return $? 2>/dev/null || exit $?
