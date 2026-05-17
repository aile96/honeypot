"""Default variables for the 5Gcore target.

Only values that are required by the generic lab pipeline or by the 5Gcore
runtime are kept here. OpenTelemetry-only feature flags and namespace settings
are intentionally not present in this target file.
"""

variables = [
    # Generic pipeline timing and build behavior.
    {'name': 'UNDERLAY_COMPOSE_WAIT_TIMEOUT_SECONDS', 'type': 'int', 'value': 120},
    {'name': 'COMPOSE_BUILD_ENABLED', 'type': 'bool', 'value': True},
    {'name': 'DOCKER_BUILD_TIMEOUT_SECONDS', 'type': 'int', 'value': 3600},

    # Temporary Docker build-helper used by Skaffold to build and push images.
    {'name': 'BUILD_HELPER_NAME', 'type': 'str', 'value': 'cluster-build-helper'},
    {'name': 'BUILD_HELPER_IMAGE', 'type': 'str', 'value': 'docker:29-dind'},
    {'name': 'BUILD_HELPER_PORT', 'type': 'int', 'value': 23750},
    {'name': 'BUILD_HELPER_CACHE_DIR', 'type': 'str', 'value': '/res/cache/build-helper'},
    {'name': 'BUILD_HELPER_READY_TIMEOUT_SECONDS', 'type': 'int', 'value': 120},

    # Shared image tag used by local wrapper images and target helper images.
    {'name': 'IMAGE_VERSION', 'type': 'str', 'value': '2.0.2'},

    # 5Gcore underlay services used by the Caldera kill chains.
    {'name': 'CALDERA_SERVER_ENABLE', 'type': 'bool', 'value': True},
    {'name': 'ATTACKER_ENABLE', 'type': 'bool', 'value': True},
    {'name': 'CALDERA_SERVER', 'type': 'str', 'value': 'caldera'},
    {'name': 'CALDERA_PORT', 'type': 'int', 'value': 8888},
    {'name': 'ATTACKER', 'type': 'str', 'value': 'attacker'},
    {'name': 'CP_NETWORK', 'type': 'str', 'value': 'kind'},
    {'name': 'COMPOSE_PROJECT_NAME', 'type': 'str', 'value': 'honeypot-underlay'},
    {'name': 'SAMBA_ENABLE', 'type': 'bool', 'value': True},

    # Intentional PCF lab surface used by 5Gcore KC2.
    {'name': 'PCF_HOST_NETWORK', 'type': 'bool', 'value': True},
    {'name': 'PCF_TEST_SERVICE_PORT', 'type': 'int', 'value': 2525},
    {'name': 'RCE_VULN', 'type': 'bool', 'value': True},
    {'name': 'SOCKET_SHARED', 'type': 'bool', 'value': True},
    {'name': 'RECURSIVE_DNS', 'type': 'bool', 'value': True},
    {'name': 'API_CERT', 'type': 'bool', 'value': True},
    {'name': 'ETCD_EXPOSURE', 'type': 'bool', 'value': True},
    {'name': 'PLAIN_PORT', 'type': 'int', 'value': 12379},

    # Reusable controller image deployed as the registry image updater.
    {'name': 'IMAGE_UPDATER_ENABLED', 'type': 'bool', 'value': True},
    {'name': 'IMAGE_UPDATER_RUN_EVERY', 'type': 'str', 'value': '1m'},

    # Local HTTPS registry used by Skaffold builds and Kind image pulls.
    {'name': 'REGISTRY_NAME', 'type': 'str', 'value': 'registry'},
    {'name': 'REGISTRY_PORT', 'type': 'int', 'value': 5000},
    {'name': 'REGISTRY_USER', 'type': 'str', 'value': 'imageuser'},
    {'name': 'REGISTRY_PASS', 'type': 'str', 'value': 'SuperPassW1!'},
    {'name': 'REGISTRY_AUTH_DIR', 'type': 'str', 'value': '/res/runtime/registry'},
    {'name': 'REGISTRY_CA_FILE', 'type': 'str', 'value': '/res/runtime/registry/certs/rootca.crt'},

    # Kind/Kubernetes cluster rendering values.
    {'name': 'KUBE_CONTEXT', 'type': 'str', 'value': ''},
    {'name': 'K8S_IMAGE', 'type': 'str', 'value': 'kindest/node:v1.30.0'},
    {'name': 'WORKERS', 'type': 'int', 'value': 2},
    {'name': 'KIND_POD_SUBNET', 'type': 'str', 'value': '10.244.0.0/16'},
    {'name': 'KIND_SERVICE_SUBNET', 'type': 'str', 'value': '10.96.0.0/12'},
    {'name': 'KIND_API_SERVER_ADDRESS', 'type': 'str', 'value': '127.0.0.1'},
    {'name': 'KIND_API_SERVER_PORT', 'type': 'str', 'value': ''},

    # MongoDB auth uses generated Kubernetes Secrets. These names are stable so
    # upgrades can reuse the same credentials and preserve existing PVC data.
    {'name': 'MONGODB_ROOT_SECRET_NAME', 'type': 'str', 'value': 'free5gc-mongodb-root'},
    {'name': 'MONGODB_APP_SECRET_NAME', 'type': 'str', 'value': 'free5gc-mongodb-app'},
    {'name': 'MONGODB_AUTH_SECRET_NAME', 'type': 'str', 'value': 'free5gc-mongodb-auth'},
    {'name': 'MONGODB_APP_USER', 'type': 'str', 'value': 'free5gc'},
    {'name': 'MONGODB_APP_DATABASE', 'type': 'str', 'value': 'free5gc'},
    {'name': 'MONGODB_AUTHSOURCE', 'type': 'str', 'value': 'free5gc'},

    # Test NWDAF registration behavior. The image is built from /workdir/common/attacker,
    # which carries the NWDAF helper scripts used by the 5Gcore chart.
    {'name': 'NWDAF_REGISTER_ON_START', 'type': 'bool', 'value': True},
    {'name': 'NWDAF_REGISTRATION_TEST_ENABLED', 'type': 'bool', 'value': True},
    {'name': 'NWDAF_REGISTRATION_TEST_SEQUENCE', 'type': 'str', 'value': 'SMF\\,AMF\\,UDM\\,PCF'},

    # Helm deployment behavior for free5GC and UERANSIM.
    {'name': 'HELM_TIMEOUT', 'type': 'str', 'value': '20m'},
]
