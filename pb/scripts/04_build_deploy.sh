#!/usr/bin/env bash
set -euo pipefail

# =========================================
# Utility functions
# =========================================
log()  { printf "\033[1;36m[INFO]\033[0m %s\n" "$*"; }
warn() { printf "\033[1;33m[WARN]\033[0m %s\n" "$*"; }
err()  { printf "\033[1;31m[ERR ]\033[0m %s\n" "$*" >&2; }
die()  { echo -e "[ERROR] $*" >&2; exit 1; }
req(){ command -v "$1" >/dev/null 2>&1 || die "Required command not found: $1"; }

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

# Optional multi-arch build (e.g., "linux/amd64,linux/arm64")
PLATFORM="${PLATFORM:-}"

# Helm
HELM_TIMEOUT="${HELM_TIMEOUT:-1h}"

# Docker helper (dedicated daemon/socket via docker:dind)
DOCKER_HELPER_IMAGE="${DOCKER_HELPER_IMAGE:-docker:26.1-dind}"
DOCKER_HELPER_NAME="${DOCKER_HELPER_NAME:-docker-cli-helper}"
WORKDIR="$(pwd)"

# Network the helper container will join
CP_NETWORK="${CP_NETWORK:-bridge}"

# Requirements
req docker
req helm
req kubectl

# =========================================
# Docker helper lifecycle
# =========================================
start_docker_helper() {
  docker rm -f "${DOCKER_HELPER_NAME}" >/dev/null 2>&1 || true

  log "Starting Docker helper '${DOCKER_HELPER_NAME}' with ${DOCKER_HELPER_IMAGE}"
  local dind_args=()
  if [[ "${INSECURE_REGISTRY}" == "true" ]]; then
    dind_args+=( "--insecure-registry=${REGISTRY_NAME}:${REGISTRY_PORT}" )
  fi

  docker run -d --rm --name "${DOCKER_HELPER_NAME}" \
    --privileged \
    --network "${CP_NETWORK}" \
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
  if [[ "${INTERNAL_REGISTRY}" == "true" ]]; then
    log "Ensuring internal registry (${REGISTRY_NAME}:${REGISTRY_PORT}) is running inside helper..."
    if ! docker exec "${DOCKER_HELPER_NAME}" docker ps --format '{{.Names}}' | grep -q "^${REGISTRY_NAME}\$"; then
      docker exec "${DOCKER_HELPER_NAME}" docker run -d --name "${REGISTRY_NAME}" \
        -p "${REGISTRY_PORT}:5000" \
        --restart=always registry:2 >/dev/null
      [[ "${INSECURE_REGISTRY}" != "true" ]] && warn "Internal registry started without TLS; set INSECURE_REGISTRY=true for HTTP pushes."
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
  log "Stopping Docker helper '${DOCKER_HELPER_NAME}'"
  docker rm -f "${DOCKER_HELPER_NAME}" >/dev/null 2>&1 || true
}

# Execute Docker CLI inside helper
d() { docker exec "${DOCKER_HELPER_NAME}" docker "$@"; }

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

# Import a specific tag from the host into the helper, only if missing in helper.
import_from_host_if_needed() {
  local tag="$1"

  if d image inspect "$tag" >/dev/null 2>&1; then
    return 0
  fi
  if docker image inspect "$tag" >/dev/null 2>&1; then
    log "Importing image from host into helper: $tag"
    docker save "$tag" | docker exec -i "${DOCKER_HELPER_NAME}" docker load
    return 0
  fi
  return 1
}

# ---------- New flow: build on host → load into helper → push ----------
load_into_helper() {
  local tag="$1"
  log "Loading image into helper: $tag"
  docker save "$tag" | docker exec -i "${DOCKER_HELPER_NAME}" docker load
}

host_build(){
  local tag="$1" ctx="$2" df="$3"; shift 3
  local -a buildargs=( "$@" )

  local abs_ctx abs_df
  abs_ctx="$(cd "$ctx" && pwd)"
  abs_df="$(cd "$(dirname "$df")" && pwd)/$(basename "$df")"

  log "Building on host -> $tag (context=$abs_ctx, dockerfile=$abs_df)"
  if [[ -n "${PLATFORM}" ]]; then
    if [[ "$PLATFORM" == *","* ]]; then
      warn "Multi-arch requested ($PLATFORM): host '--load' is not supported for multi-arch. Falling back to helper buildx with direct push."
      # Build multi-arch directly inside the helper and push
      d buildx create --use --name honeypotbx >/dev/null 2>&1 || true
      d buildx build --platform "$PLATFORM" -t "$tag" "$abs_ctx" -f "$abs_df" "${buildargs[@]}" --push
      return 2  # signal: already pushed from helper
    else
      docker buildx create --use --name hostbx >/dev/null 2>&1 || true
      docker buildx build --platform "$PLATFORM" -t "$tag" "$abs_ctx" -f "$abs_df" "${buildargs[@]}" --load
    fi
  else
    docker build -t "$tag" "$abs_ctx" -f "$abs_df" "${buildargs[@]}"
  fi
  return 0
}

host_build_and_push(){
  local tag="$1" ctx="$2" df="$3"; shift 3
  local -a buildargs=( "$@" )

  if ! host_build "$tag" "$ctx" "$df" "${buildargs[@]}"; then
    # host_build returned non-zero (unexpected)
    return 1
  fi

  # If host_build returned code 2 (multi-arch fallback), it already pushed
  local rc=$?
  if [[ $rc -eq 2 ]]; then
    return 0
  fi

  load_into_helper "$tag"
  d push "$tag"
}

ensure_image(){
  local name="$1" ctx="$2" df="$3"; shift 3
  local -a buildargs=( "$@" )
  local tag; tag="$(tag_for "$name")"

  # Force rebuild/push
  if [[ "$BUILD_CONTAINERS_K8S" == "true" ]]; then
    host_build_and_push "$tag" "$ctx" "$df" "${buildargs[@]}"; return
  fi

  # Already in remote registry?
  if remote_image_exists "$tag"; then
    log "Skipping build ($name): already in registry -> $tag"; return
  fi

  # Present in helper's daemon?
  if local_image_exists "$tag"; then
    log "Pushing only (found locally in helper, missing remotely): $tag"
    d push "$tag"; return
  fi

  # Try to import from host now (just-in-time)
  if import_from_host_if_needed "$tag"; then
    log "Pushing only (imported from host): $tag"
    d push "$tag"; return
  fi

  # Otherwise, build on host → load into helper → push
  host_build_and_push "$tag" "$ctx" "$df" "${buildargs[@]}"
}

# =========================================
# Build/push images
# =========================================
build_all_images(){
  ensure_image accounting . src/accounting/Dockerfile
  ensure_image ad . src/ad/Dockerfile

  ensure_image attacker src/attacker src/attacker/Dockerfile \
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

  ensure_image auth src/auth src/auth/Dockerfile
  ensure_image cart . src/cart/src/Dockerfile
  ensure_image checkout . src/checkout/Dockerfile
  ensure_image controller src/controller src/controller/Dockerfile
  ensure_image currency . src/currency/Dockerfile
  ensure_image email . src/email/Dockerfile
  ensure_image flagd . src/flagd/Dockerfile
  ensure_image flagd-ui . src/flagd-ui/Dockerfile
  ensure_image fraud-detection . src/fraud-detection/Dockerfile
  ensure_image frontend . src/frontend/Dockerfile
  ensure_image frontend-proxy . src/frontend-proxy/Dockerfile
  ensure_image image-provider . src/image-provider/Dockerfile
  ensure_image kafka . src/kafka/Dockerfile
  ensure_image payment . src/payment/Dockerfile

  ensure_image postgres src/postgres src/postgres/Dockerfile --build-arg DB="curr"
  ensure_image postgres-auth src/postgres src/postgres/Dockerfile --build-arg DB="auth"
  ensure_image postgres-payment src/postgres src/postgres/Dockerfile --build-arg DB="pay"

  ensure_image product-catalog . src/product-catalog/Dockerfile
  ensure_image quote . src/quote/Dockerfile
  ensure_image recommendation . src/recommendation/Dockerfile
  ensure_image shipping . src/shipping/Dockerfile
  ensure_image sidecar-enc src/sidecar-enc src/sidecar-enc/Dockerfile
  ensure_image sidecar-mal src/sidecar-mal src/sidecar-mal/Dockerfile
  ensure_image smtp src/smtp src/smtp/Dockerfile
  ensure_image valkey-cart . src/valkey-cart/Dockerfile
  ensure_image traffic-translator src/traffic-translator src/traffic-translator/Dockerfile
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
  helm upgrade --install honeypot-additions helm-charts/additions \
    --wait --timeout "${HELM_TIMEOUT}" \
    -f helm-charts/additions/values.yaml \
    --set default.image.repository="${REGISTRY_NAME}:${REGISTRY_PORT}" \
    --set default.image.version="${IMAGE_VERSION}" \
    --set networkPolicies.enabled=true \
    --set postgres.enabled=true \
    --set config.enabled=true \
    --set RBAC.enabled=true \
    --set namespaces.enabled=true \
    --set pool.enabled=true \
    --set volumes.enabled=$([[ "${SAMBA_ENABLE:-true}" == "true" ]] && echo true || echo false) \
    --set registryAuth.enabled=true \
    --set "networkPolicies.rules.dmz.name=${DMZ_NAMESPACE}" \
    --set "networkPolicies.rules.dmz.ingress=${APP_NAMESPACE}\, ${MEM_NAMESPACE}\, ${TST_NAMESPACE}\, kube-system" \
    --set "networkPolicies.rules.dmz.egress=${APP_NAMESPACE}\, ${MEM_NAMESPACE}\, ${TST_NAMESPACE}\, kube-system" \
    --set "networkPolicies.rules.pay.name=${PAY_NAMESPACE}" \
    --set "networkPolicies.rules.pay.ingress=${APP_NAMESPACE}\, ${MEM_NAMESPACE}\, ${DAT_NAMESPACE}\, kube-system" \
    --set "networkPolicies.rules.pay.egress=${APP_NAMESPACE}\, ${MEM_NAMESPACE}\, ${DAT_NAMESPACE}\, kube-system" \
    --set "networkPolicies.rules.app.name=${APP_NAMESPACE}" \
    --set "networkPolicies.rules.app.ingress=${DAT_NAMESPACE}\, ${MEM_NAMESPACE}\, ${DMZ_NAMESPACE}\, ${PAY_NAMESPACE}\, kube-system" \
    --set "networkPolicies.rules.app.egress=${DAT_NAMESPACE}\, ${MEM_NAMESPACE}\, ${DMZ_NAMESPACE}\, ${PAY_NAMESPACE}\, kube-system" \
    --set "networkPolicies.rules.dat.name=${DAT_NAMESPACE}" \
    --set "networkPolicies.rules.dat.ingress=${APP_NAMESPACE}\, ${MEM_NAMESPACE}\, ${PAY_NAMESPACE}\, kube-system" \
    --set "networkPolicies.rules.dat.egress=${APP_NAMESPACE}\, ${MEM_NAMESPACE}\, ${PAY_NAMESPACE}\, kube-system" \
    --set "networkPolicies.rules.mem.name=${MEM_NAMESPACE}" \
    --set "networkPolicies.rules.mem.ingress=${DAT_NAMESPACE}\, ${APP_NAMESPACE}\, ${DMZ_NAMESPACE}\, ${PAY_NAMESPACE}\, ${TST_NAMESPACE}\, kube-system" \
    --set "networkPolicies.rules.mem.egress=${DAT_NAMESPACE}\, ${APP_NAMESPACE}\, ${DMZ_NAMESPACE}\, ${PAY_NAMESPACE}\, ${TST_NAMESPACE}\, kube-system" \
    --set "networkPolicies.rules.tst.name=${TST_NAMESPACE}" \
    --set "networkPolicies.rules.tst.ingress=${MEM_NAMESPACE}\, ${DMZ_NAMESPACE}\, kube-system" \
    --set "networkPolicies.rules.tst.egress=${MEM_NAMESPACE}\, ${DMZ_NAMESPACE}\, kube-system" \
    --set "postgres.namespace=${DAT_NAMESPACE}" \
    --set "config.objects.flagd-credentials-ui.namespace=${MEM_NAMESPACE}" \
    --set "config.objects.proto.namespace=${APP_NAMESPACE}" \
    --set "config.objects.product-catalog-products.namespace=${APP_NAMESPACE}" \
    --set "config.objects.flagd-config.namespace=${MEM_NAMESPACE}" \
    --set "config.objects.dbcurrency-creds.namespace=${APP_NAMESPACE}" \
    --set "config.objects.dbcurrency.namespace=${DAT_NAMESPACE}" \
    --set "config.objects.dbpayment.namespace=${DAT_NAMESPACE}" \
    --set "config.objects.dbauth.namespace=${DAT_NAMESPACE}" \
    --set "config.objects.smb-creds.namespace=${MEM_NAMESPACE}" \
    --set "namespaces.list=${APP_NAMESPACE}\, ${DMZ_NAMESPACE}\, ${DAT_NAMESPACE}\, ${PAY_NAMESPACE}\, ${MEM_NAMESPACE}\, ${TST_NAMESPACE}" \
    --set "registryAuth.username=${REGISTRY_USER:-}" \
    --set "registryAuth.password=${REGISTRY_PASS:-}" \
    --set "pool.ips=${FRONTEND_PROXY_IP:-}-${FRONTEND_PROXY_IP:-}" \
    --set vulnerabilities.dnsGrant=$([[ "${DNS_GRANT:-true}" == "true" ]] && echo true || echo false) \
    --set vulnerabilities.deployGrant=$([[ "${DEPLOY_GRANT:-true}" == "true" ]] && echo true || echo false) \
    --set vulnerabilities.anonymousGrant=$([[ "${ANONYMOUS_AUTH:-}" == "true" && "${ANONYMOUS_GRANT:-}" == "true" ]] && echo true || echo false) \
    --set vulnerabilities.currencyGrant=$([[ "${CURRENCY_GRANT:-true}" == "true" ]] && echo true || echo false) \
    --set config.objects.flagd-credentials-ui.type=$([[ "${FLAGD_CONFIGMAP:-true}" == "true" ]] && echo configmap || echo secret)

  # honeypot-telemetry
  local TELEMETRY_VALUES="helm-charts/telemetry/values-$( [[ \"${LOG_OPEN:-true}\" == \"true\" ]] && echo noauth || echo auth ).yaml"
  helm upgrade --install honeypot-telemetry helm-charts/telemetry \
    --namespace "${MEM_NAMESPACE}" --create-namespace \
    --wait --timeout "${HELM_TIMEOUT}" \
    -f "$TELEMETRY_VALUES" \
    --set opentelemetry-collector.enabled=true \
    --set jaeger.enabled=true \
    --set prometheus.enabled=true \
    --set grafana.enabled=true \
    --set opensearch.enabled=true \
    --set "opentelemetry-collector.config.receivers.httpcheck/frontend-proxy.targets[0].endpoint=http://frontend-proxy.${DMZ_NAMESPACE}:8080" \
    --set "opentelemetry-collector.config.receivers.redis.endpoint=valkey-cart.${DAT_NAMESPACE}:6379"

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
    --set "components.traffic-controller.env[4].value=$( [[ \"${LOG_TOKEN:-true}\" == \"true\" ]] && echo synthetic-log.sh || echo null.sh )" \
    --set "components.image-updater.enabled=$( [[ \"${AUTO_DEPLOY:-true}\" == \"true\" ]] && echo true || echo false )" \
    --set "components.smtp.hostNetwork=$( [[ \"${HOST_NETWORK:-true}\" == \"true\" ]] && echo true || echo false )" \
    --set "components.smtp.env[0].value=$( [[ \"${RCE_VULN:-true}\" == \"true\" ]] && echo true || echo false )" \
    --set "components.smtp.additionalVolumes[0].hostPath.path=$( [[ \"${SOCKET_SHARED:-true}\" == \"true\" ]] && echo $CRICTL_RUNTIME_PATH || echo /tmp/disabled-containerd.sock )" \
    --set "components.smtp.additionalVolumes[0].hostPath.type=$( [[ \"${SOCKET_SHARED:-true}\" == \"true\" ]] && echo Socket || echo FileOrCreate )" \
    --set "components.smtp.volumeMounts[0].mountPath=/host/run/containerd/containerd.sock" \
    --set "components.currency.env[9].value=$( [[ \"${FLAGD_FEATURES:-true}\" == \"true\" ]] && echo true || echo false )" \
    --set "components.checkout.sidecarContainers[0].env[7].value=$( [[ \"${FLAGD_FEATURES:-true}\" == \"true\" ]] && echo true || echo false )" \
    --set "components.checkout.sidecarContainers[1].env[7].value=$( [[ \"${FLAGD_FEATURES:-true}\" == \"true\" ]] && echo true || echo false )" \
    --set "components.frontend.sidecarContainers[0].env[7].value=$( [[ \"${FLAGD_FEATURES:-true}\" == \"true\" ]] && echo true || echo false )" \
    --set "components.payment.sidecarContainers[0].env[7].value=$( [[ \"${FLAGD_FEATURES:-true}\" == \"true\" ]] && echo true || echo false )" \
    --set "components.flagd.sidecarContainers[0].envSwitchFrom[0].valueFrom=$( [[ \"${FLAGD_CONFIGMAP:-true}\" == \"true\" ]] && echo configmap || echo secret )" \
    --set "components.flagd.sidecarContainers[0].envSwitchFrom[1].valueFrom=$( [[ \"${FLAGD_CONFIGMAP:-true}\" == \"true\" ]] && echo configmap || echo secret )"
}

# =========================================
# Main
# =========================================
trap 'stop_docker_helper' EXIT

start_docker_helper

log "== Docker images =="
build_all_images

log "== Helm Deploy =="
deploy_helm

stop_docker_helper
log "Done. Registry=${REGISTRY_NAME}:${REGISTRY_PORT} | Version=${IMAGE_VERSION} | BUILD_CONTAINERS_K8S=${BUILD_CONTAINERS_K8S} | INTERNAL_REGISTRY=${INTERNAL_REGISTRY} | INSECURE_REGISTRY=${INSECURE_REGISTRY}"
