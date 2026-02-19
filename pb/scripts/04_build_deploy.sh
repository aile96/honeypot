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

# Optional multi-arch build (e.g., "linux/amd64,linux/arm64")
PLATFORM="${PLATFORM:-}"

# Helm
HELM_TIMEOUT="${HELM_TIMEOUT:-1h}"

# Docker helper (dedicated daemon/socket via docker:dind)
DOCKER_HELPER_IMAGE="${DOCKER_HELPER_IMAGE:-docker:26.1-dind}"
DOCKER_HELPER_NAME="${DOCKER_HELPER_NAME:-docker-cli-helper}"
WORKDIR="${PROJECT_ROOT}"

# Network the helper container will join
CP_NETWORK="${CP_NETWORK:-bridge}"

normalize_bool_var INTERNAL_REGISTRY
normalize_bool_var INSECURE_REGISTRY
normalize_bool_var BUILD_CONTAINERS_K8S
[[ "${DOCKER_BUILD_PARALLELISM}" =~ ^[0-9]+$ ]] || die "DOCKER_BUILD_PARALLELISM must be an integer >= 1"
(( DOCKER_BUILD_PARALLELISM >= 1 )) || die "DOCKER_BUILD_PARALLELISM must be >= 1"
[[ "${DOCKER_BUILD_RETRY_ATTEMPTS}" =~ ^[0-9]+$ ]] || die "DOCKER_BUILD_RETRY_ATTEMPTS must be an integer >= 1"
(( DOCKER_BUILD_RETRY_ATTEMPTS >= 1 )) || die "DOCKER_BUILD_RETRY_ATTEMPTS must be >= 1"
[[ "${DOCKER_BUILD_RETRY_DELAY_SECONDS}" =~ ^[0-9]+$ ]] || die "DOCKER_BUILD_RETRY_DELAY_SECONDS must be an integer >= 0"
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
  local dind_args=()
  if is_true "${INSECURE_REGISTRY}"; then
    dind_args+=( "--insecure-registry=${REGISTRY_NAME}:${REGISTRY_PORT}" )
  fi

  retry_cmd "${DOCKER_BUILD_RETRY_ATTEMPTS}" "${DOCKER_BUILD_RETRY_DELAY_SECONDS}" "docker run ${DOCKER_HELPER_NAME}" \
    docker run -d --rm --name "${DOCKER_HELPER_NAME}" \
      --privileged \
      --network "${CP_NETWORK}" \
      -v "${WORKDIR}:${WORKDIR}:ro" \
      -v "${DOCKER_HELPER_NAME}-data:/var/lib/docker" \
      "${DOCKER_HELPER_IMAGE}" \
      "${dind_args[@]}" >/dev/null

  # Wait for helper's Docker daemon
  log "Waiting for helper's Docker daemon..."
  for i in {1..60}; do
    if docker exec "${DOCKER_HELPER_NAME}" docker info >/dev/null 2>&1; then
      break
    fi
    sleep 1
    [[ $i -eq 60 ]] && die "Helper Docker daemon did not become ready in time"
  done

  # Install registry CA so HTTPS pushes succeed
  install_registry_ca

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
      d buildx build --platform "${PLATFORM}" -t "${tag}" -f "${abs_df}" "${buildargs[@]}" "${abs_ctx}" --push
  else
    retry_cmd "${DOCKER_BUILD_RETRY_ATTEMPTS}" "${DOCKER_BUILD_RETRY_DELAY_SECONDS}" "docker build ${tag}" \
      d build -t "${tag}" -f "${abs_df}" "${buildargs[@]}" "${abs_ctx}"
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

  queue_image_build accounting accounting . src/accounting/Dockerfile
  queue_image_build ad ad . src/ad/Dockerfile

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
  queue_image_build cart cart . src/cart/src/Dockerfile
  queue_image_build checkout checkout . src/checkout/Dockerfile
  queue_image_build controller controller src/controller src/controller/Dockerfile
  queue_image_build currency currency . src/currency/Dockerfile
  queue_image_build email email . src/email/Dockerfile
  queue_image_build flagd flagd . src/flagd/Dockerfile
  queue_image_build flagd-ui flagd-ui . src/flagd-ui/Dockerfile
  queue_image_build fraud-detection fraud-detection . src/fraud-detection/Dockerfile
  queue_image_build frontend frontend . src/frontend/Dockerfile
  queue_image_build frontend-proxy frontend-proxy . src/frontend-proxy/Dockerfile
  queue_image_build image-provider image-provider . src/image-provider/Dockerfile
  queue_image_build kafka kafka . src/kafka/Dockerfile
  queue_image_build payment payment . src/payment/Dockerfile

  queue_image_build postgres postgres src/postgres src/postgres/Dockerfile --build-arg DB="curr"
  queue_image_build postgres-auth postgres-auth src/postgres src/postgres/Dockerfile --build-arg DB="auth"
  queue_image_build postgres-payment postgres-payment src/postgres src/postgres/Dockerfile --build-arg DB="pay"

  queue_image_build product-catalog product-catalog . src/product-catalog/Dockerfile
  queue_image_build quote quote . src/quote/Dockerfile
  queue_image_build recommendation recommendation . src/recommendation/Dockerfile
  queue_image_build shipping shipping . src/shipping/Dockerfile
  queue_image_build sidecar-enc sidecar-enc src/sidecar-enc src/sidecar-enc/Dockerfile
  queue_image_build sidecar-mal sidecar-mal src/sidecar-mal src/sidecar-mal/Dockerfile
  queue_image_build smtp smtp src/smtp src/smtp/Dockerfile
  queue_image_build valkey-cart valkey-cart . src/valkey-cart/Dockerfile
  queue_image_build traffic-translator traffic-translator src/traffic-translator src/traffic-translator/Dockerfile

  for i in "${!pids[@]}"; do
    if ! wait "${pids[$i]}"; then
      failed+=("${labels[$i]}")
    fi
  done

  if (( ${#failed[@]} > 0 )); then
    die "Image build/push failed for: ${failed[*]}"
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

  # metallb
  helm upgrade --install metallb metallb/metallb \
    --namespace metallb-system --create-namespace \
    --wait --timeout "${HELM_TIMEOUT}"

  # csi-driver-smb
  if [ "${SAMBA_ENABLE:-true}" = "true" ]; then
    helm upgrade --install csi-driver-smb csi-driver-smb/csi-driver-smb \
      --namespace kube-system \
      --wait --timeout "${HELM_TIMEOUT}"
  fi

  # honeypot-additions
  local additions_overrides
  additions_overrides="$(mktemp)"
  trap_add "rm -f '${additions_overrides}'" EXIT
  cat > "${additions_overrides}" <<EOF
default:
  image:
    repository: "${REGISTRY_NAME}:${REGISTRY_PORT}"
    version: "${IMAGE_VERSION}"
networkPolicies:
  enabled: true
  rules:
    dmz:
      name: "${DMZ_NAMESPACE}"
      ingress: "${APP_NAMESPACE}, ${MEM_NAMESPACE}, ${TST_NAMESPACE}, kube-system"
      egress: "${APP_NAMESPACE}, ${MEM_NAMESPACE}, ${TST_NAMESPACE}, kube-system"
    pay:
      name: "${PAY_NAMESPACE}"
      ingress: "${APP_NAMESPACE}, ${MEM_NAMESPACE}, ${DAT_NAMESPACE}, kube-system"
      egress: "${APP_NAMESPACE}, ${MEM_NAMESPACE}, ${DAT_NAMESPACE}, kube-system"
    app:
      name: "${APP_NAMESPACE}"
      ingress: "${DAT_NAMESPACE}, ${MEM_NAMESPACE}, ${DMZ_NAMESPACE}, ${PAY_NAMESPACE}, kube-system"
      egress: "${DAT_NAMESPACE}, ${MEM_NAMESPACE}, ${DMZ_NAMESPACE}, ${PAY_NAMESPACE}, kube-system"
    dat:
      name: "${DAT_NAMESPACE}"
      ingress: "${APP_NAMESPACE}, ${MEM_NAMESPACE}, ${PAY_NAMESPACE}, kube-system"
      egress: "${APP_NAMESPACE}, ${MEM_NAMESPACE}, ${PAY_NAMESPACE}, kube-system"
    mem:
      name: "${MEM_NAMESPACE}"
      ingress: "${DAT_NAMESPACE}, ${APP_NAMESPACE}, ${DMZ_NAMESPACE}, ${PAY_NAMESPACE}, ${TST_NAMESPACE}, kube-system"
      egress: "${DAT_NAMESPACE}, ${APP_NAMESPACE}, ${DMZ_NAMESPACE}, ${PAY_NAMESPACE}, ${TST_NAMESPACE}, kube-system"
    tst:
      name: "${TST_NAMESPACE}"
      ingress: "${MEM_NAMESPACE}, ${DMZ_NAMESPACE}, kube-system"
      egress: "${MEM_NAMESPACE}, ${DMZ_NAMESPACE}, kube-system"
postgres:
  enabled: true
  namespace: "${DAT_NAMESPACE}"
config:
  enabled: true
  objects:
    flagd-credentials-ui:
      namespace: "${MEM_NAMESPACE}"
      type: "$(bool_text FLAGD_CONFIGMAP true configmap secret)"
    proto:
      namespace: "${APP_NAMESPACE}"
    product-catalog-products:
      namespace: "${APP_NAMESPACE}"
    flagd-config:
      namespace: "${MEM_NAMESPACE}"
    dbcurrency-creds:
      namespace: "${APP_NAMESPACE}"
    dbcurrency:
      namespace: "${DAT_NAMESPACE}"
    dbpayment:
      namespace: "${DAT_NAMESPACE}"
    dbauth:
      namespace: "${DAT_NAMESPACE}"
    smb-creds:
      namespace: "${MEM_NAMESPACE}"
RBAC:
  enabled: true
namespaces:
  enabled: true
  list: "${APP_NAMESPACE}, ${DMZ_NAMESPACE}, ${DAT_NAMESPACE}, ${PAY_NAMESPACE}, ${MEM_NAMESPACE}, ${TST_NAMESPACE}"
pool:
  enabled: true
  ips: "${FRONTEND_PROXY_IP:-}-${GENERIC_SVC_ADDR:-}"
volumes:
  enabled: $(helm_bool SAMBA_ENABLE true)
registryAuth:
  enabled: true
  username: "${REGISTRY_USER:-}"
  password: "${REGISTRY_PASS:-}"
vulnerabilities:
  dnsGrant: $(helm_bool DNS_GRANT true)
  deployGrant: $(helm_bool DEPLOY_GRANT true)
  anonymousGrant: $(helm_bool_all ANONYMOUS_AUTH false ANONYMOUS_GRANT false)
  currencyGrant: $(helm_bool CURRENCY_GRANT true)
EOF

  helm upgrade --install honeypot-additions helm-charts/additions \
    --wait --timeout "${HELM_TIMEOUT}" \
    -f helm-charts/additions/values.yaml \
    -f "${additions_overrides}"

  # honeypot-telemetry
  local TELEMETRY_VALUES telemetry_overrides
  TELEMETRY_VALUES="helm-charts/telemetry/values-$(bool_text LOG_OPEN true noauth auth).yaml"
  telemetry_overrides="$(mktemp)"
  trap_add "rm -f '${telemetry_overrides}'" EXIT
  cat > "${telemetry_overrides}" <<EOF
opentelemetry-collector:
  enabled: true
  config:
    receivers:
      "httpcheck/frontend-proxy":
        targets:
          - endpoint: "http://frontend-proxy.${DMZ_NAMESPACE}:8080"
      redis:
        endpoint: "valkey-cart.${DAT_NAMESPACE}:6379"
jaeger:
  enabled: true
prometheus:
  enabled: true
grafana:
  enabled: true
opensearch:
  enabled: true
EOF

  helm upgrade --install honeypot-telemetry helm-charts/telemetry \
    --namespace "${MEM_NAMESPACE}" --create-namespace \
    --wait --timeout "${HELM_TIMEOUT}" \
    -f "${TELEMETRY_VALUES}" \
    -f "${telemetry_overrides}"

  # honeypot-astronomy-shop
  helm upgrade --install honeypot-astronomy-shop helm-charts/astronomy-shop \
    --wait --timeout "${HELM_TIMEOUT}" \
    -f helm-charts/astronomy-shop/values.yaml \
    --set-string default.image.repository="${REGISTRY_NAME}:${REGISTRY_PORT}" \
    --set-string default.image.tag="${IMAGE_VERSION}" \
    --set components.test-image.enabled=true \
    --set components.frontend-proxy.enabled=true \
    --set components.image-provider.enabled=true \
    --set components.valkey-cart.enabled=true \
    --set components.payment.enabled=true \
    --set components.flagd.enabled=true \
    --set components.traffic-controller.enabled=true \
    --set components.accounting.enabled=true \
    --set components.ad.enabled=true \
    --set components.auth.enabled=true \
    --set components.cart.enabled=true \
    --set components.checkout.enabled=true \
    --set components.currency.enabled=true \
    --set components.email.enabled=true \
    --set components.fraud-detection.enabled=true \
    --set components.frontend.enabled=true \
    --set components.product-catalog.enabled=true \
    --set components.quote.enabled=true \
    --set components.recommendation.enabled=true \
    --set components.shipping.enabled=true \
    --set components.smtp.enabled=true \
    --set components.kafka.enabled=true \
    --set "default.env[1].value=otel-collector.${MEM_NAMESPACE}" \
    --set "components.accounting.namespace=${APP_NAMESPACE}" \
    --set "components.ad.namespace=${APP_NAMESPACE}" \
    --set "components.ad.env[1].value=flagd.${MEM_NAMESPACE}" \
    --set "components.auth.namespace=${APP_NAMESPACE}" \
    --set "components.auth.env[0].value=postgres-auth.${DAT_NAMESPACE}" \
    --set "components.auth.initContainers[0].env[0].value=postgres-auth.${DAT_NAMESPACE}" \
    --set "components.cart.namespace=${APP_NAMESPACE}" \
    --set "components.cart.env[2].value=valkey-cart.${DAT_NAMESPACE}:6379" \
    --set "components.cart.env[3].value=flagd.${MEM_NAMESPACE}" \
    --set "components.cart.initContainers[0].env[0].value=valkey-cart.${DAT_NAMESPACE}" \
    --set "components.checkout.namespace=${APP_NAMESPACE}" \
    --set "components.checkout.env[8].value=flagd.${MEM_NAMESPACE}" \
    --set "components.checkout.initContainers[1].env[0].value=flagd.${MEM_NAMESPACE}" \
    --set "components.checkout.sidecarContainers[0].imageOverride.repository=${REGISTRY_NAME}:${REGISTRY_PORT}/sidecar-enc" \
    --set "components.checkout.sidecarContainers[0].env[3].value=flagd.${MEM_NAMESPACE}" \
    --set "components.checkout.sidecarContainers[1].imageOverride.repository=${REGISTRY_NAME}:${REGISTRY_PORT}/sidecar-enc" \
    --set "components.checkout.sidecarContainers[1].env[2].value=payment.${PAY_NAMESPACE}:8080" \
    --set "components.checkout.sidecarContainers[1].env[3].value=flagd.${MEM_NAMESPACE}" \
    --set "components.currency.namespace=${APP_NAMESPACE}" \
    --set "components.currency.env[4].value=postgres.${DAT_NAMESPACE}" \
    --set "components.currency.env[10].value=flagd.${MEM_NAMESPACE}" \
    --set "components.currency.initContainers[0].env[0].value=postgres.${DAT_NAMESPACE}" \
    --set "components.email.namespace=${APP_NAMESPACE}" \
    --set "components.fraud-detection.namespace=${APP_NAMESPACE}" \
    --set "components.fraud-detection.env[1].value=flagd.${MEM_NAMESPACE}" \
    --set "components.frontend.namespace=${APP_NAMESPACE}" \
    --set "components.frontend.env[10].value=flagd.${MEM_NAMESPACE}" \
    --set "components.frontend.initContainers[0].env[0].value=flagd.${MEM_NAMESPACE}" \
    --set "components.frontend.sidecarContainers[0].imageOverride.repository=${REGISTRY_NAME}:${REGISTRY_PORT}/sidecar-enc" \
    --set "components.frontend.sidecarContainers[0].env[3].value=flagd.${MEM_NAMESPACE}" \
    --set "components.frontend-proxy.namespace=${DMZ_NAMESPACE}" \
    --set "components.frontend-proxy.service.loadBalancerIP=${FRONTEND_PROXY_IP:-}" \
    --set "components.frontend-proxy.env[1].value=flagd.${MEM_NAMESPACE}" \
    --set "components.frontend-proxy.env[3].value=flagd.${MEM_NAMESPACE}" \
    --set "components.frontend-proxy.env[5].value=frontend.${APP_NAMESPACE}" \
    --set "components.frontend-proxy.env[7].value=grafana.${MEM_NAMESPACE}" \
    --set "components.frontend-proxy.env[11].value=jaeger-query.${MEM_NAMESPACE}" \
    --set "components.image-provider.namespace=${DMZ_NAMESPACE}" \
    --set "components.image-updater.namespace=${MEM_NAMESPACE}" \
    --set "components.image-updater.imageOverride.repository=${REGISTRY_NAME}:${REGISTRY_PORT}/controller" \
    --set "components.image-updater.env[2].value=${REGISTRY_NAME}:${REGISTRY_PORT}" \
    --set "components.image-updater.env[3].value=${REGISTRY_USER:-}" \
    --set "components.image-updater.env[4].value=${REGISTRY_PASS:-}" \
    --set "components.payment.namespace=${PAY_NAMESPACE}" \
    --set "components.payment.env[1].value=flagd.${MEM_NAMESPACE}" \
    --set "components.payment.env[4].value=postgres-payment.${DAT_NAMESPACE}" \
    --set "components.payment.initContainers[0].env[0].value=flagd.${MEM_NAMESPACE}" \
    --set "components.payment.initContainers[1].env[0].value=postgres-payment.${DAT_NAMESPACE}" \
    --set "components.payment.sidecarContainers[0].imageOverride.repository=${REGISTRY_NAME}:${REGISTRY_PORT}/sidecar-enc" \
    --set "components.payment.sidecarContainers[0].env[3].value=flagd.${MEM_NAMESPACE}" \
    --set "components.product-catalog.namespace=${APP_NAMESPACE}" \
    --set "components.product-catalog.env[2].value=flagd.${MEM_NAMESPACE}" \
    --set "components.quote.namespace=${APP_NAMESPACE}" \
    --set "components.recommendation.namespace=${APP_NAMESPACE}" \
    --set "components.recommendation.env[4].value=flagd.${MEM_NAMESPACE}" \
    --set "components.shipping.namespace=${APP_NAMESPACE}" \
    --set "components.smtp.namespace=${DMZ_NAMESPACE}" \
    --set "components.flagd.namespace=${MEM_NAMESPACE}" \
    --set "components.kafka.namespace=${APP_NAMESPACE}" \
    --set "components.test-image.namespace=${TST_NAMESPACE}" \
    --set "components.test-image.imageOverride.repository=${REGISTRY_NAME}:${REGISTRY_PORT}/attacker" \
    --set "components.test-image.env[0].value=http://${CALDERA_SERVER:-}:8888" \
    --set "components.test-image.env[2].value=${MEM_NAMESPACE}" \
    --set "components.test-image.env[3].value=${DMZ_NAMESPACE}" \
    --set "components.test-image.env[4].value=${APP_NAMESPACE}" \
    --set "components.test-image.env[5].value=${PAY_NAMESPACE}" \
    --set "components.test-image.env[6].value=${ATTACKER:-}" \
    --set "components.test-image.env[7].value=${KC0101:-}" \
    --set "components.test-image.env[8].value=${KC0102:-}" \
    --set "components.test-image.env[9].value=${KC0103:-}" \
    --set "components.test-image.env[10].value=${KC0104:-}" \
    --set "components.test-image.env[11].value=${KC0105:-}" \
    --set "components.test-image.env[12].value=${KC0106:-}" \
    --set "components.test-image.env[13].value=${KC0107:-}" \
    --set "components.test-image.env[14].value=${KC0108:-}" \
    --set "components.traffic-controller.namespace=${MEM_NAMESPACE}" \
    --set "components.traffic-controller.imageOverride.repository=${REGISTRY_NAME}:${REGISTRY_PORT}/controller" \
    --set "components.traffic-controller.sidecarContainers[0].imageOverride.repository=${REGISTRY_NAME}:${REGISTRY_PORT}/traffic-translator" \
    --set "components.valkey-cart.namespace=${DAT_NAMESPACE}" \
    --set "components.traffic-controller.env[4].value=$(bool_text LOG_TOKEN true synthetic-log.sh null.sh)" \
    --set "components.image-updater.enabled=$(helm_bool AUTO_DEPLOY true)" \
    --set "components.smtp.hostNetwork=$(helm_bool HOST_NETWORK true)" \
    --set "components.smtp.env[0].value=$(helm_bool RCE_VULN true)" \
    --set "components.smtp.additionalVolumes[0].hostPath.path=$(bool_text SOCKET_SHARED true "$CRICTL_RUNTIME_PATH" /tmp/disabled-containerd.sock)" \
    --set "components.smtp.additionalVolumes[0].hostPath.type=$(bool_text SOCKET_SHARED true Socket FileOrCreate)" \
    --set "components.smtp.volumeMounts[0].mountPath=/host/run/containerd/containerd.sock" \
    --set "components.currency.env[9].value=$(helm_bool FLAGD_FEATURES true)" \
    --set "components.checkout.sidecarContainers[0].env[7].value=$(helm_bool FLAGD_FEATURES true)" \
    --set "components.checkout.sidecarContainers[1].env[7].value=$(helm_bool FLAGD_FEATURES true)" \
    --set "components.frontend.sidecarContainers[0].env[7].value=$(helm_bool FLAGD_FEATURES true)" \
    --set "components.payment.sidecarContainers[0].env[7].value=$(helm_bool FLAGD_FEATURES true)" \
    --set "components.flagd.sidecarContainers[0].envSwitchFrom[0].valueFrom=$(bool_text FLAGD_CONFIGMAP true configmap secret)" \
    --set "components.flagd.sidecarContainers[0].envSwitchFrom[1].valueFrom=$(bool_text FLAGD_CONFIGMAP true configmap secret)"
}

# =========================================
# Main
# =========================================
trap_add stop_docker_helper EXIT

start_docker_helper

log "== Docker images =="
build_all_images

log "== Helm Deploy =="
deploy_helm

stop_docker_helper
log "Done. Registry=${REGISTRY_NAME}:${REGISTRY_PORT} | Version=${IMAGE_VERSION} | BUILD_CONTAINERS_K8S=${BUILD_CONTAINERS_K8S} | INTERNAL_REGISTRY=${INTERNAL_REGISTRY} | INSECURE_REGISTRY=${INSECURE_REGISTRY}"
