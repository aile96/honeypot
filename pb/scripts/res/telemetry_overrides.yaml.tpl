opentelemetry-collector:
  enabled: true
  resources:
    requests:
      cpu: "${OTEL_COLLECTOR_CPU_REQUEST:-200m}"
      memory: "${OTEL_COLLECTOR_MEMORY_REQUEST:-256Mi}"
    limits:
      cpu: "${OTEL_COLLECTOR_CPU_LIMIT:-1000m}"
      memory: "${OTEL_COLLECTOR_MEMORY_LIMIT:-512Mi}"
  config:
    receivers:
      "httpcheck/frontend-proxy":
        targets:
          - endpoint: "http://frontend-proxy.${DMZ_NAMESPACE}:8080"
      redis:
        endpoint: "valkey-cart.${DAT_NAMESPACE}:6379"
    processors:
      memory_limiter:
        check_interval: "${OTEL_MEMORY_LIMITER_CHECK_INTERVAL:-2s}"
        limit_percentage: ${OTEL_MEMORY_LIMITER_LIMIT_PERCENTAGE:-85}
        spike_limit_percentage: ${OTEL_MEMORY_LIMITER_SPIKE_LIMIT_PERCENTAGE:-20}
    exporters:
      otlp:
        endpoint: "jaeger-collector.${MEM_NAMESPACE}.svc.cluster.local:4317"
        sending_queue:
          enabled: true
          queue_size: ${OTEL_EXPORTER_OTLP_QUEUE_SIZE:-512}
          num_consumers: ${OTEL_EXPORTER_OTLP_QUEUE_CONSUMERS:-4}
        retry_on_failure:
          enabled: true
          initial_interval: "${OTEL_EXPORTER_OTLP_RETRY_INITIAL_INTERVAL:-1s}"
          max_interval: "${OTEL_EXPORTER_OTLP_RETRY_MAX_INTERVAL:-10s}"
          max_elapsed_time: "${OTEL_EXPORTER_OTLP_RETRY_MAX_ELAPSED_TIME:-30s}"
    service:
      pipelines:
        traces:
          exporters: [otlp, spanmetrics]
        metrics:
          exporters: [otlphttp/prometheus]
        logs:
          exporters: [opensearch]
      telemetry:
        metrics:
          readers:
            - periodic:
                interval: 10000
                timeout: 5000
                exporter:
                  otlp:
                    protocol: grpc
                    endpoint: "otel-collector.${MEM_NAMESPACE}:4317"
jaeger:
  enabled: true
  allInOne:
    args:
      - "--memory.max-traces=${JAEGER_MEMORY_MAX_TRACES:-2000}"
      - "--query.base-path=/jaeger/ui"
      - "--prometheus.server-url=http://prometheus:9090"
      - "--prometheus.query.normalize-calls=true"
      - "--prometheus.query.normalize-duration=true"
    resources:
      requests:
        cpu: "${JAEGER_CPU_REQUEST:-100m}"
        memory: "${JAEGER_MEMORY_REQUEST:-256Mi}"
      limits:
        cpu: "${JAEGER_CPU_LIMIT:-1000m}"
        memory: "${JAEGER_MEMORY_LIMIT:-768Mi}"
prometheus:
  enabled: true
grafana:
  enabled: true
opensearch:
  enabled: true
