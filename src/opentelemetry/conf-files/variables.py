"""Default variables for the OpenTelemetry target.

Variables are grouped by purpose so target-specific settings are easier to
audit. The generic lab controller loads this list into CONFIG and passes only
approved values to pipeline steps, hooks, Compose, Kind, Helm, and Skaffold.
"""

variables = [
    # Generic pipeline timing and build behavior.
    {'name': 'UNDERLAY_COMPOSE_WAIT_TIMEOUT_SECONDS', 'type': 'int', 'value': 120},
    {'name': 'COMPOSE_BUILD_ENABLED', 'type': 'bool', 'value': True},
    {'name': 'DOCKER_BUILD_PARALLELISM', 'type': 'int', 'value': 4},
    {'name': 'DOCKER_BUILD_TIMEOUT_SECONDS', 'type': 'int', 'value': 0},
    {'name': 'IMAGE_VERSION', 'type': 'str', 'value': '2.0.2'},

    # Temporary Docker build-helper used by Skaffold and local image builds.
    {'name': 'BUILD_HELPER_NAME', 'type': 'str', 'value': 'docker-cli-helper'},
    {'name': 'BUILD_HELPER_IMAGE', 'type': 'str', 'value': 'docker:26.1-dind'},
    {'name': 'BUILD_HELPER_PORT', 'type': 'int', 'value': 23750},
    {'name': 'BUILD_HELPER_CACHE_DIR', 'type': 'str', 'value': '/res/cache/build-helper'},
    {'name': 'BUILD_HELPER_READY_TIMEOUT_SECONDS', 'type': 'int', 'value': 120},

    # Underlay service toggles and service names.
    {'name': 'LOAD_GENERATOR_ENABLE', 'type': 'bool', 'value': True},
    {'name': 'SAMBA_ENABLE', 'type': 'bool', 'value': True},
    {'name': 'PROXY_ENABLE', 'type': 'bool', 'value': True},
    {'name': 'ATTACKER_ENABLE', 'type': 'bool', 'value': True},
    {'name': 'CALDERA_SERVER_ENABLE', 'type': 'bool', 'value': True},
    {'name': 'CILIUM_ENABLED', 'type': 'bool', 'value': True},
    {'name': 'CALDERA_SERVER', 'type': 'str', 'value': 'caldera'},
    {'name': 'CALDERA_PORT', 'type': 'int', 'value': 8888},
    {'name': 'ATTACKER', 'type': 'str', 'value': 'attacker'},
    {'name': 'PROXY', 'type': 'str', 'value': 'router'},
    {'name': 'GENERIC_SVC_PORT', 'type': 'int', 'value': 8085},
    {'name': 'GENERIC_SVC_ADDR', 'type': 'str', 'value': '127.0.0.1'},
    {'name': 'FRONTEND_PROXY_IP', 'type': 'str', 'value': '127.0.0.1'},
    {'name': 'CP_NETWORK', 'type': 'str', 'value': 'kind'},
    {'name': 'COMPOSE_PROJECT_NAME', 'type': 'str', 'value': 'honeypot-underlay'},

    # Control-plane and Kubernetes API exposure settings used by attack scenarios.
    {'name': 'CONTROL_PLANE_NODE', 'type': 'str', 'value': 'kind-control-plane'},
    {'name': 'CONTROL_PLANE_PORT', 'type': 'int', 'value': 6443},
    {'name': 'KUBESERVER_PORT', 'type': 'int', 'value': 6443},
    {'name': 'KUBE_APISERVER_IPS', 'type': 'str', 'value': 'auto'},
    {'name': 'KUBE_APISERVER_CIDRS', 'type': 'str', 'value': ''},
    {'name': 'API_CERT', 'type': 'bool', 'value': True},
    {'name': 'OPEN_PORTS', 'type': 'bool', 'value': True},
    {'name': 'ETCD_EXPOSURE', 'type': 'bool', 'value': True},
    {'name': 'PLAIN_PORT', 'type': 'int', 'value': 12379},
    {'name': 'APISERVER_WAIT_TIMEOUT', 'type': 'int', 'value': 300},
    {'name': 'SA_NAMESPACE', 'type': 'str', 'value': 'kube-system'},
    {'name': 'SA_NAME', 'type': 'str', 'value': 'controller-admin'},
    {'name': 'CRB_NAME', 'type': 'str', 'value': 'controller-admin'},

    # Local HTTPS registry used by Compose, Skaffold, attackers, and Kind nodes.
    {'name': 'REGISTRY_NAME', 'type': 'str', 'value': 'registry'},
    {'name': 'REGISTRY_PORT', 'type': 'int', 'value': 5000},
    {'name': 'REGISTRY_USER', 'type': 'str', 'value': 'testuser'},
    {'name': 'REGISTRY_PASS', 'type': 'str', 'value': 'testpassword'},
    {'name': 'REGISTRY_AUTH_DIR', 'type': 'str', 'value': '/res/runtime/registry'},
    {'name': 'REGISTRY_CA_FILE', 'type': 'str', 'value': '/res/runtime/registry/certs/rootca.crt'},

    # Kind cluster rendering values.
    {'name': 'KUBE_CONTEXT', 'type': 'str', 'value': ''},
    {'name': 'K8S_IMAGE', 'type': 'str', 'value': 'kindest/node:v1.30.0'},
    {'name': 'WORKERS', 'type': 'int', 'value': 2},
    {'name': 'KIND_POD_SUBNET', 'type': 'str', 'value': '10.244.0.0/16'},
    {'name': 'KIND_SERVICE_SUBNET', 'type': 'str', 'value': '10.96.0.0/12'},
    {'name': 'KIND_API_SERVER_ADDRESS', 'type': 'str', 'value': '127.0.0.1'},
    {'name': 'KIND_API_SERVER_PORT', 'type': 'str', 'value': ''},

    # Helm deployment behavior and application namespaces.
    {'name': 'HELM_TIMEOUT', 'type': 'str', 'value': '20m'},
    {'name': 'APP_NAMESPACE', 'type': 'str', 'value': 'app'},
    {'name': 'DAT_NAMESPACE', 'type': 'str', 'value': 'dat'},
    {'name': 'DMZ_NAMESPACE', 'type': 'str', 'value': 'dmz'},
    {'name': 'MEM_NAMESPACE', 'type': 'str', 'value': 'mem'},
    {'name': 'PAY_NAMESPACE', 'type': 'str', 'value': 'pay'},
    {'name': 'TST_NAMESPACE', 'type': 'str', 'value': 'tst'},

    # Continuous telemetry export settings collected during kill-chain runs.
    {'name': 'TELEMETRY_EXPORT_ENABLED', 'type': 'bool', 'value': True},
    {'name': 'TELEMETRY_EXPORT_INTERVAL_SEC', 'type': 'int', 'value': 60},
    {'name': 'TELEMETRY_OUTPUT_ROOT', 'type': 'str', 'value': '/results/telemetry'},
    {'name': 'TELEMETRY_LOGS_LIMIT', 'type': 'int', 'value': 0},
    {'name': 'TELEMETRY_LOGS_BATCH_SIZE', 'type': 'int', 'value': 5000},
    {'name': 'TELEMETRY_TRACE_LIMIT', 'type': 'int', 'value': 5000},
    {'name': 'TELEMETRY_METRICS_QUERY', 'type': 'str', 'value': '{__name__!=""}'},
    {'name': 'TELEMETRY_METRICS_STEP', 'type': 'str', 'value': '15s'},

    # Intentional OpenTelemetry lab weaknesses and feature flags.
    {'name': 'MISSING_POLICY', 'type': 'bool', 'value': True},
    {'name': 'RECURSIVE_DNS', 'type': 'bool', 'value': True},
    {'name': 'LOG_OPEN', 'type': 'bool', 'value': True},
    {'name': 'LOG_TOKEN', 'type': 'bool', 'value': True},
    {'name': 'DNS_GRANT', 'type': 'bool', 'value': True},
    {'name': 'DEPLOY_GRANT', 'type': 'bool', 'value': True},
    {'name': 'AUTO_DEPLOY', 'type': 'bool', 'value': True},
    {'name': 'ANONYMOUS_GRANT', 'type': 'bool', 'value': True},
    {'name': 'FLAGD_CONFIGMAP', 'type': 'bool', 'value': True},
    {'name': 'FLAGD_FEATURES', 'type': 'bool', 'value': True},
    {'name': 'CURRENCY_GRANT', 'type': 'bool', 'value': True},
    {'name': 'RCE_VULN', 'type': 'bool', 'value': True},
    {'name': 'HOST_NETWORK', 'type': 'bool', 'value': True},
    {'name': 'ANONYMOUS_AUTH', 'type': 'bool', 'value': True},
]
