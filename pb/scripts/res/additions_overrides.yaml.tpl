default:
  image:
    repository: "${REGISTRY_NAME}:${REGISTRY_PORT}"
    version: "${IMAGE_VERSION}"
networkPolicies:
  enabled: true
  allowKubeAPIServer: true
  kubeAPIServer:
    ingressIPs: ${KUBE_APISERVER_IPS}
    egressIPs: ${KUBE_APISERVER_IPS}
  internet:
    mode: world
    ingressCidrs: []
    egressCidrs: []
    exceptCidrs: []
  rules:
    dmz:
      name: "${DMZ_NAMESPACE}"
      ingress: "${APP_NAMESPACE}, ${MEM_NAMESPACE}, ${TST_NAMESPACE}, kube-system"
      egress: "${APP_NAMESPACE}, ${MEM_NAMESPACE}, ${TST_NAMESPACE}, kube-system"
      allowInternetEgress: true
      allowInternetIngress: true
    pay:
      name: "${PAY_NAMESPACE}"
      ingress: "${APP_NAMESPACE}, ${MEM_NAMESPACE}, ${DAT_NAMESPACE}, kube-system"
      egress: "${APP_NAMESPACE}, ${MEM_NAMESPACE}, ${DAT_NAMESPACE}, kube-system"
      allowInternetEgress: false
      allowInternetIngress: false
    app:
      name: "${APP_NAMESPACE}"
      ingress: "${DAT_NAMESPACE}, ${MEM_NAMESPACE}, ${DMZ_NAMESPACE}, ${PAY_NAMESPACE}, kube-system"
      egress: "${DAT_NAMESPACE}, ${MEM_NAMESPACE}, ${DMZ_NAMESPACE}, ${PAY_NAMESPACE}, kube-system"
      allowInternetEgress: false
      allowInternetIngress: false
      podSelector:
        matchExpressions:
          - key: app.kubernetes.io/name
            operator: NotIn
            values: ["currency"]
    dat:
      name: "${DAT_NAMESPACE}"
      ingress: "${APP_NAMESPACE}, ${MEM_NAMESPACE}, ${PAY_NAMESPACE}, kube-system"
      egress: "${APP_NAMESPACE}, ${MEM_NAMESPACE}, ${PAY_NAMESPACE}, kube-system"
      allowInternetEgress: false
      allowInternetIngress: false
    mem:
      name: "${MEM_NAMESPACE}"
      ingress: "${DAT_NAMESPACE}, ${APP_NAMESPACE}, ${DMZ_NAMESPACE}, ${PAY_NAMESPACE}, ${TST_NAMESPACE}, kube-system"
      egress: "${DAT_NAMESPACE}, ${APP_NAMESPACE}, ${DMZ_NAMESPACE}, ${PAY_NAMESPACE}, ${TST_NAMESPACE}, kube-system"
      allowInternetEgress: true
      allowInternetIngress: true
      podSelector:
        matchExpressions:
          - key: app.kubernetes.io/name
            operator: NotIn
            values: ["flagd", "traffic-controller"]
    tst:
      name: "${TST_NAMESPACE}"
      ingress: "${MEM_NAMESPACE}, ${DMZ_NAMESPACE}, kube-system"
      egress: "${MEM_NAMESPACE}, ${DMZ_NAMESPACE}, kube-system"
      allowInternetEgress: true
      allowInternetIngress: true
      podSelector:
        matchExpressions:
          - key: app.kubernetes.io/name
            operator: NotIn
            values: ["test-image"]
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
