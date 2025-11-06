#!/usr/bin/env bash
set -euo pipefail

# ==========================
# Utility functions
# ==========================
log()  { printf "\033[1;36m[INFO]\033[0m %s\n" "$*"; }
warn() { printf "\033[1;33m[WARN]\033[0m %s\n" "$*"; }
err()  { printf "\033[1;31m[ERR ]\033[0m %s\n" "$*" >&2; }
die()  { echo -e "[ERROR] $*" >&2; exit 1; }
req(){ command -v "$1" >/dev/null 2>&1 || die "Required command not found: $1"; }

### ========= Parameters =========
IMAGE_VERSION="${IMAGE_VERSION:-2.0.2}"
REGISTRY_NAME="${REGISTRY_NAME:-registry}"
REGISTRY_PORT="${REGISTRY_PORT:-5000}"
APP_NAMESPACE="${APP_NAMESPACE:-app}"
DAT_NAMESPACE="${DAT_NAMESPACE:-dat}"
DMZ_NAMESPACE="${DMZ_NAMESPACE:-dmz}"
MEM_NAMESPACE="${MEM_NAMESPACE:-mem}"
PAY_NAMESPACE="${PAY_NAMESPACE:-pay}"
TST_NAMESPACE="${TST_NAMESPACE:-tst}"

# Build: true = force rebuild and push; false = build only if missing from registry (or push if only local)
BUILD_CONTAINERS_K8S="${BUILD_CONTAINERS_K8S:-false}"

# Optional: multi-arch build (e.g. "linux/amd64,linux/arm64")
PLATFORM="${PLATFORM:-}"

# Helm
HELM_TIMEOUT="${HELM_TIMEOUT:-1h}"

req docker
req helm
req kubectl

### ========= Image Helpers =========
tag_for(){ echo "${REGISTRY_NAME}:${REGISTRY_PORT}/$1:${IMAGE_VERSION}"; }

remote_image_exists(){
  docker manifest inspect "$1" >/dev/null 2>&1
}

local_image_exists(){
  docker image inspect "$1" >/dev/null 2>&1
}

build_and_push(){
  local tag="$1" ctx="$2" df="$3"; shift 3
  local -a buildargs=( "$@" )

  log "Building and pushing -> $tag (context=$ctx, dockerfile=$df)"
  if [[ -n "$PLATFORM" ]]; then
    docker buildx create --use --name honeypotbx >/dev/null 2>&1 || true
    docker buildx build --platform "$PLATFORM" -t "$tag" "$ctx" -f "$df" "${buildargs[@]}" --push
  else
    docker build -t "$tag" "$ctx" -f "$df" "${buildargs[@]}"
    docker push "$tag"
  fi
}

ensure_image(){
  local name="$1" ctx="$2" df="$3"; shift 3
  local -a buildargs=( "$@" )
  local tag; tag="$(tag_for "$name")"

  if [[ "$BUILD_CONTAINERS_K8S" == "true" ]]; then
    build_and_push "$tag" "$ctx" "$df" "${buildargs[@]}"; return
  fi

  if remote_image_exists "$tag"; then
    log "Skipping build ($name): tag already exists in registry -> $tag"; return
  fi

  if local_image_exists "$tag"; then
    log "Pushing only (found locally, missing remotely): $tag"
    docker push "$tag"; return
  fi

  build_and_push "$tag" "$ctx" "$df" "${buildargs[@]}"
}

### ========= Build/push images =========
build_all_images(){
  # accounting
  ensure_image accounting . src/accounting/Dockerfile

  # ad
  ensure_image ad . src/ad/Dockerfile

  # attacker (build args from env)
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

  # auth
  ensure_image auth src/auth src/auth/Dockerfile

  # cart
  ensure_image cart . src/cart/src/Dockerfile

  # checkout
  ensure_image checkout . src/checkout/Dockerfile

  # controller
  ensure_image controller src/controller src/controller/Dockerfile

  # currency
  ensure_image currency . src/currency/Dockerfile

  # email
  ensure_image email . src/email/Dockerfile

  # flagd
  ensure_image flagd . src/flagd/Dockerfile

  # flagd-ui
  ensure_image flagd-ui . src/flagd-ui/Dockerfile

  # fraud-detection
  ensure_image fraud-detection . src/fraud-detection/Dockerfile

  # frontend
  ensure_image frontend . src/frontend/Dockerfile

  # frontend-proxy
  ensure_image frontend-proxy . src/frontend-proxy/Dockerfile

  # image-provider
  ensure_image image-provider . src/image-provider/Dockerfile

  # kafka
  ensure_image kafka . src/kafka/Dockerfile

  # payment
  ensure_image payment . src/payment/Dockerfile

  # postgres variants
  ensure_image postgres src/postgres src/postgres/Dockerfile --build-arg DB="curr"
  ensure_image postgres-auth src/postgres src/postgres/Dockerfile --build-arg DB="auth"
  ensure_image postgres-payment src/postgres src/postgres/Dockerfile --build-arg DB="pay"

  # product-catalog
  ensure_image product-catalog . src/product-catalog/Dockerfile

  # quote
  ensure_image quote . src/quote/Dockerfile

  # recommendation
  ensure_image recommendation . src/recommendation/Dockerfile

  # shipping
  ensure_image shipping . src/shipping/Dockerfile

  # sidecars
  ensure_image sidecar-enc src/sidecar-enc src/sidecar-enc/Dockerfile
  ensure_image sidecar-mal src/sidecar-mal src/sidecar-mal/Dockerfile

  # smtp
  ensure_image smtp src/smtp src/smtp/Dockerfile

  # valkey-cart
  ensure_image valkey-cart . src/valkey-cart/Dockerfile

  # traffic-translator
  ensure_image traffic-translator src/traffic-translator src/traffic-translator/Dockerfile
}

### ========= Helm Deploy =========
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
  helm upgrade --install csi-driver-smb csi-driver-smb/csi-driver-smb \
    --namespace kube-system \
    --wait --timeout "${HELM_TIMEOUT}"

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
    --set volumes.enabled=true \
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
    --set "networkPolicies.rules.mem.ingress=${DAT_NAMESPACE}\, ${MEM_NAMESPACE}\, ${DMZ_NAMESPACE}\, ${PAY_NAMESPACE}\, ${TST_NAMESPACE}\, kube-system" \
    --set "networkPolicies.rules.mem.egress=${DAT_NAMESPACE}\, ${MEM_NAMESPACE}\, ${DMZ_NAMESPACE}\, ${PAY_NAMESPACE}\, ${TST_NAMESPACE}\, kube-system" \
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
    --set "components.smtp.additionalVolumes[0].hostPath.path=$( [[ \"${SOCKET_SHARED:-true}\" == \"true\" ]] && echo /run/containerd/containerd.sock || echo /tmp/disabled-containerd.sock )" \
    --set "components.smtp.additionalVolumes[0].hostPath.type=$( [[ \"${SOCKET_SHARED:-true}\" == \"true\" ]] && echo Socket || echo FileOrCreate )" \
    --set "components.currency.env[9].value=$( [[ \"${FLAGD_FEATURES:-true}\" == \"true\" ]] && echo true || echo false )" \
    --set "components.checkout.sidecarContainers[0].env[7].value=$( [[ \"${FLAGD_FEATURES:-true}\" == \"true\" ]] && echo true || echo false )" \
    --set "components.checkout.sidecarContainers[1].env[7].value=$( [[ \"${FLAGD_FEATURES:-true}\" == \"true\" ]] && echo true || echo false )" \
    --set "components.frontend.sidecarContainers[0].env[7].value=$( [[ \"${FLAGD_FEATURES:-true}\" == \"true\" ]] && echo true || echo false )" \
    --set "components.payment.sidecarContainers[0].env[7].value=$( [[ \"${FLAGD_FEATURES:-true}\" == \"true\" ]] && echo true || echo false )" \
    --set "components.flagd.sidecarContainers[0].envSwitchFrom[0].valueFrom=$( [[ \"${FLAGD_CONFIGMAP:-true}\" == \"true\" ]] && echo configmap || echo secret )" \
    --set "components.flagd.sidecarContainers[0].envSwitchFrom[1].valueFrom=$( [[ \"${FLAGD_CONFIGMAP:-true}\" == \"true\" ]] && echo configmap || echo secret )"
}

### ========= Main =========
log "== Docker images =="
build_all_images

log "== Helm Deploy =="
deploy_helm

log "Done. Registry=${REGISTRY_NAME}:${REGISTRY_PORT} | Version=${IMAGE_VERSION} | BUILD_CONTAINERS_K8S=${BUILD_CONTAINERS_K8S}"