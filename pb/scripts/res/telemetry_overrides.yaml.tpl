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
