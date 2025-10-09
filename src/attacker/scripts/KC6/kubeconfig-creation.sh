#!/usr/bin/env bash
set -euo pipefail

# ================== Parameters ==================
APISERVER_HOST="${APISERVER_HOST:-kind-cluster-control-plane}"
APISERVER_IP="$(command -v dig >/dev/null 2>&1 && dig +short "$APISERVER_HOST" A | head -n1 || true)"
APISERVER="${APISERVER:-https://${APISERVER_IP:-$APISERVER_HOST}:6443}"

NAMESPACE="${NAMESPACE:-kube-system}"
SA_NAME="${SA_NAME:-ops-admin}"
CRB_NAME="${CRB_NAME:-ops-admin-crb}"

CLUSTER_NAME="${CLUSTER_NAME:-local-cluster}"
USER_NAME="${USER_NAME:-ops-admin}"
CONTEXT_NAME="${CONTEXT_NAME:-ops-admin@local}"
KUBECONFIG_OUT="${KUBECONFIG_OUT:-$DATA_PATH/KC6/ops-admin.kubeconfig}"

# TLS: WITHOUT CA
CURL_TLS=(-k)

# Auth header: if ADMIN_TOKEN in not null, use it; otherwise anonymous
if [[ -n "${ADMIN_TOKEN:-}" ]]; then
  HDR_AUTH=(-H "Authorization: Bearer ${ADMIN_TOKEN}" -H "Content-Type: application/json")
else
  HDR_AUTH=(-H "Content-Type: application/json")
fi

# ================== Utils ==================
json_post() {
  # $1=url  $2=payload
  local url="$1" data="$2" resp status body
  resp=$(curl -sS "${CURL_TLS[@]}" "${HDR_AUTH[@]}" -X POST "$url" -d "$data" -w $'\n%{http_code}')
  status="${resp##*$'\n'}"
  body="${resp%$'\n'*}"
  echo "$status"
  printf '%s' "$body" > /tmp/body.json
}

ok_or_409() {
  # $1=status-code  $2=what
  if [[ "$1" =~ ^2[0-9][0-9]$ ]]; then
    echo "[+] $2: creato"
  elif [[ "$1" == "409" ]]; then
    echo "[=] $2: already existing (ok)"
  else
    echo "[-] $2: HTTP $1"; cat /tmp/body.json; exit 1
  fi
}

need_bin() { command -v "$1" >/dev/null 2>&1 || { echo "[-] Needed '$1'"; exit 1; }; }

# ================== Precheck ==================
need_bin curl
need_bin jq

mkdir -p "$(dirname -- "$KUBECONFIG_OUT")"

# (Optional) download kubectl if not present
if ! command -v kubectl >/dev/null 2>&1; then
  echo "[*] Downloading kubectl..."
  curl -fsSLo /usr/local/bin/kubectl "https://dl.k8s.io/release/$(curl -L -s https://dl.k8s.io/release/stable.txt)/bin/linux/amd64/kubectl"
  chmod +x /usr/local/bin/kubectl
fi

echo "[i] API server: $APISERVER"
echo "[i] Namespace:  $NAMESPACE"
echo "[i] SA:         $SA_NAME"
echo "[i] CRB:        $CRB_NAME"

# ================== Crea SA ==================
echo "[*] Creating ServiceAccount (if missing)..."
status=$(json_post \
  "${APISERVER}/api/v1/namespaces/${NAMESPACE}/serviceaccounts" \
  "$(jq -n --arg name "$SA_NAME" '{apiVersion:"v1",kind:"ServiceAccount",metadata:{name:$name}}')"
)
ok_or_409 "$status" "ServiceAccount ${NAMESPACE}/${SA_NAME}"

# ================== Crea CRB ==================
echo "[*] Creating ClusterRoleBinding (if missing)..."
status=$(json_post \
  "${APISERVER}/apis/rbac.authorization.k8s.io/v1/clusterrolebindings" \
  "$(jq -n --arg crb "$CRB_NAME" --arg sa "$SA_NAME" --arg ns "$NAMESPACE" '
    {
      apiVersion:"rbac.authorization.k8s.io/v1",
      kind:"ClusterRoleBinding",
      metadata:{name:$crb},
      roleRef:{apiGroup:"rbac.authorization.k8s.io",kind:"ClusterRole",name:"cluster-admin"},
      subjects:[{kind:"ServiceAccount",name:$sa,namespace:$ns}]
    }')"
)
ok_or_409 "$status" "ClusterRoleBinding ${CRB_NAME}"

# ================== TokenRequest ==================
echo "[*] Request token bound for SA..."
status=$(json_post \
  "${APISERVER}/api/v1/namespaces/${NAMESPACE}/serviceaccounts/${SA_NAME}/token" \
  '{"apiVersion":"authentication.k8s.io/v1","kind":"TokenRequest","spec":{"expirationSeconds":360000}}'
)
if [[ "$status" != "201" && "$status" != "200" ]]; then
  echo "[-] TokenRequest HTTP $status"; cat /tmp/body.json; exit 1
fi
SA_TOKEN=$(jq -r '.status.token // empty' /tmp/body.json)
if [[ -z "$SA_TOKEN" || "$SA_TOKEN" == "null" ]]; then
  echo "[-] Token not obtained:"; cat /tmp/body.json; exit 1
fi

# ================== Quick verification of token ==================
echo "[*] Verifying token with /api..."
http=$(curl -sS "${CURL_TLS[@]}" -o /dev/null -w "%{http_code}" -H "Authorization: Bearer ${SA_TOKEN}" "${APISERVER}/api" || true)
if [[ "$http" != "200" && "$http" != "403" ]]; then
  echo "[-] Token not accepted by API server (HTTP $http)"; exit 1
fi

# ================== kubeconfig (insecure) ==================
echo "[*] Generation of kubeconfig: ${KUBECONFIG_OUT}"
cat > "${KUBECONFIG_OUT}" <<EOF
apiVersion: v1
kind: Config
clusters:
- name: ${CLUSTER_NAME}
  cluster:
    server: ${APISERVER}
    insecure-skip-tls-verify: true
users:
- name: ${USER_NAME}
  user:
    token: ${SA_TOKEN}
contexts:
- name: ${CONTEXT_NAME}
  context:
    cluster: ${CLUSTER_NAME}
    user: ${USER_NAME}
current-context: ${CONTEXT_NAME}
EOF

echo "[+] Done"
echo "    KUBECONFIG=${KUBECONFIG_OUT} kubectl auth can-i '*' '*' --all-namespaces"
echo "    KUBECONFIG=${KUBECONFIG_OUT} kubectl get ns"
