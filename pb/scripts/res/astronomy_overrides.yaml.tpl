default:
  image:
    repository: "${REGISTRY_NAME}:${REGISTRY_PORT}"
    tag: "${IMAGE_VERSION}"
  envOverrides:
    - name: OTEL_COLLECTOR_NAME
      value: "otel-collector.${MEM_NAMESPACE}"
components:
  accounting:
    enabled: true
    namespace: "${APP_NAMESPACE}"
  ad:
    enabled: true
    namespace: "${APP_NAMESPACE}"
    envOverrides:
      - name: FLAGD_HOST
        value: "flagd.${MEM_NAMESPACE}"
  auth:
    enabled: true
    namespace: "${APP_NAMESPACE}"
    envOverrides:
      - name: DB_HOST
        value: "postgres-auth.${DAT_NAMESPACE}"
  cart:
    enabled: true
    namespace: "${APP_NAMESPACE}"
    envOverrides:
      - name: VALKEY_ADDR
        value: "valkey-cart.${DAT_NAMESPACE}:6379"
      - name: FLAGD_HOST
        value: "flagd.${MEM_NAMESPACE}"
  checkout:
    enabled: true
    namespace: "${APP_NAMESPACE}"
    envOverrides:
      - name: FLAGD_HOST
        value: "flagd.${MEM_NAMESPACE}"
  currency:
    enabled: true
    namespace: "${APP_NAMESPACE}"
    envOverrides:
      - name: DB_HOST
        value: "postgres.${DAT_NAMESPACE}"
      - name: EXPOSE_USE_FLAGD
        value: "$(helm_bool FLAGD_FEATURES true)"
      - name: FLAGD_HOST
        value: "flagd.${MEM_NAMESPACE}"
  email:
    enabled: true
    namespace: "${APP_NAMESPACE}"
  fraud-detection:
    enabled: true
    namespace: "${APP_NAMESPACE}"
    envOverrides:
      - name: FLAGD_HOST
        value: "flagd.${MEM_NAMESPACE}"
  frontend:
    enabled: true
    namespace: "${APP_NAMESPACE}"
    envOverrides:
      - name: FLAGD_HOST
        value: "flagd.${MEM_NAMESPACE}"
  frontend-proxy:
    enabled: true
    namespace: "${DMZ_NAMESPACE}"
    service:
      loadBalancerIP: "${FRONTEND_PROXY_IP:-}"
    envOverrides:
      - name: FLAGD_HOST
        value: "flagd.${MEM_NAMESPACE}"
      - name: FLAGD_UI_HOST
        value: "flagd.${MEM_NAMESPACE}"
      - name: FRONTEND_HOST
        value: "frontend.${APP_NAMESPACE}"
      - name: GRAFANA_HOST
        value: "grafana.${MEM_NAMESPACE}"
      - name: JAEGER_HOST
        value: "jaeger-query.${MEM_NAMESPACE}"
  image-provider:
    enabled: true
    namespace: "${DMZ_NAMESPACE}"
  image-updater:
    enabled: $(helm_bool AUTO_DEPLOY true)
    namespace: "${MEM_NAMESPACE}"
    imageOverride:
      repository: "${REGISTRY_NAME}:${REGISTRY_PORT}/controller"
    envOverrides:
      - name: REGISTRY
        value: "${REGISTRY_NAME}:${REGISTRY_PORT}"
      - name: REGISTRY_USERNAME
        value: "${REGISTRY_USER:-}"
      - name: REGISTRY_PASSWORD
        value: "${REGISTRY_PASS:-}"
  kafka:
    enabled: true
    namespace: "${APP_NAMESPACE}"
  payment:
    enabled: true
    namespace: "${PAY_NAMESPACE}"
    envOverrides:
      - name: FLAGD_HOST
        value: "flagd.${MEM_NAMESPACE}"
      - name: PGHOST
        value: "postgres-payment.${DAT_NAMESPACE}"
  product-catalog:
    enabled: true
    namespace: "${APP_NAMESPACE}"
    envOverrides:
      - name: FLAGD_HOST
        value: "flagd.${MEM_NAMESPACE}"
  quote:
    enabled: true
    namespace: "${APP_NAMESPACE}"
  recommendation:
    enabled: true
    namespace: "${APP_NAMESPACE}"
    envOverrides:
      - name: FLAGD_HOST
        value: "flagd.${MEM_NAMESPACE}"
  shipping:
    enabled: true
    namespace: "${APP_NAMESPACE}"
  smtp:
    enabled: true
    namespace: "${DMZ_NAMESPACE}"
    hostNetwork: $(helm_bool HOST_NETWORK true)
    envOverrides:
      - name: RCE_ENABLED
        value: "$(helm_bool RCE_VULN true)"
  flagd:
    enabled: true
    namespace: "${MEM_NAMESPACE}"
  test-image:
    enabled: true
    namespace: "${TST_NAMESPACE}"
    imageOverride:
      repository: "${REGISTRY_NAME}:${REGISTRY_PORT}/attacker"
    envOverrides:
      - name: CALDERA_URL
        value: "http://${CALDERA_SERVER:-}:8888"
      - name: LOG_NS
        value: "${MEM_NAMESPACE}"
      - name: ATTACKED_NS
        value: "${DMZ_NAMESPACE}"
      - name: AUTH_NS
        value: "${APP_NAMESPACE}"
      - name: NSPAYMT
        value: "${PAY_NAMESPACE}"
      - name: ATTACKERADDR
        value: "${ATTACKER:-}"
      - name: KC0101
        value: |-
          ${KC0101:-}
      - name: KC0102
        value: |-
          ${KC0102:-}
      - name: KC0103
        value: |-
          ${KC0103:-}
      - name: KC0104
        value: |-
          ${KC0104:-}
      - name: KC0105
        value: |-
          ${KC0105:-}
      - name: KC0106
        value: |-
          ${KC0106:-}
      - name: KC0107
        value: |-
          ${KC0107:-}
      - name: KC0108
        value: |-
          ${KC0108:-}
  traffic-controller:
    enabled: true
    namespace: "${MEM_NAMESPACE}"
    imageOverride:
      repository: "${REGISTRY_NAME}:${REGISTRY_PORT}/controller"
    envOverrides:
      - name: SCRIPT_NAME
        value: "$(bool_text LOG_TOKEN true synthetic-log.sh null.sh)"
  valkey-cart:
    enabled: true
    namespace: "${DAT_NAMESPACE}"
