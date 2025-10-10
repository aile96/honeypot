#!/usr/bin/env bash
set -euo pipefail

# ===== Config =====
API_SERVER="${API_SERVER:-https://kind-cluster-control-plane:6443}"
CERT_PATH="${CERT_PATH:-/tmp/KCData/KC5/kubelet-client-current-kind-cluster-worker.pem}"
CACERT_OPT="${CACERT_OPT:---insecure}"           # use --cacert /path/ca.crt if you prefer
MODE="${MODE:-ready}"                             # ready | notready
SLEEP_SECS="${SLEEP_SECS:-1}"
FORCE_CS="${FORCE_CS:-true}"

# ===== Derive NODE_NAME from the certificate CN =====
NODE_NAME="$(openssl x509 -in "$CERT_PATH" -noout -subject \
  | sed -n 's/^subject=.*CN *= *system:node:\([^,/]*\).*/\1/p' | head -n1)"
if [[ -z "$NODE_NAME" ]]; then
  echo "ERROR: unable to extract NODE_NAME from certificate CN ($CERT_PATH)" >&2
  exit 1
fi

echo "API: $API_SERVER"
echo "NODE: $NODE_NAME"
echo "MODE: $MODE"

# ===== Helpers =====
k8s_curl() {
  # usage: k8s_curl METHOD PATH [curl-args...]
  local method="$1"; shift
  local path="$1"; shift
  curl --silent --show-error --retry 2 --retry-connrefused \
    -X "$method" \
    "$API_SERVER$path" \
    --cert "$CERT_PATH" --key "$CERT_PATH" $CACERT_OPT \
    -H 'Accept: application/json' \
    "$@"
}

now_rfc3339_ns() { date -u +"%Y-%m-%dT%H:%M:%S.%NZ"; }
now_rfc3339()    { date -u +"%Y-%m-%dT%H:%M:%SZ";   }

# ===== Lease: create/update =====
ensure_lease() {
  local now; now="$(now_rfc3339_ns)"
  # try to GET the lease
  local body; body="$(k8s_curl GET "/apis/coordination.k8s.io/v1/namespaces/kube-node-lease/leases/$NODE_NAME")" || true
  if echo "$body" | jq -e '.metadata.name' >/dev/null 2>&1; then
    # PATCH merge renewTime/holderIdentity
    k8s_curl PATCH "/apis/coordination.k8s.io/v1/namespaces/kube-node-lease/leases/$NODE_NAME" \
      -H 'Content-Type: application/merge-patch+json' \
      --data "{\"spec\":{\"renewTime\":\"$now\",\"holderIdentity\":\"$NODE_NAME\"}}" >/dev/null 2>&1 || true
  else
    # CREATE (POST on the collection)
    k8s_curl POST "/apis/coordination.k8s.io/v1/namespaces/kube-node-lease/leases" \
      -H 'Content-Type: application/json' \
      --data @<(cat <<JSON
{"apiVersion":"coordination.k8s.io/v1","kind":"Lease",
 "metadata":{"name":"$NODE_NAME","namespace":"kube-node-lease"},
 "spec":{"holderIdentity":"$NODE_NAME","leaseDurationSeconds":40,"renewTime":"$now","leaseTransitions":0}}
JSON
) >/dev/null 2>&1 || true
  fi
}

# ===== Node.status Ready True/False =====
patch_node_status() {
  local st reason msg; local t1 t2
  if [[ "$MODE" == "notready" ]]; then
    st="False"; reason="ManualNotReady"; msg="Marked NotReady by updater"
  else
    st="True";  reason="ManualReady";    msg="Marked Ready by updater"
  fi
  t1="$(now_rfc3339)"; t2="$t1"
  k8s_curl PATCH "/api/v1/nodes/$NODE_NAME/status" \
    -H 'Content-Type: application/strategic-merge-patch+json' \
    --data @<(cat <<JSON
{"status":{"conditions":[
  {"type":"Ready","status":"$st","reason":"$reason","message":"$msg",
   "lastHeartbeatTime":"$t1","lastTransitionTime":"$t2"}
]}}
JSON
) >/dev/null 2>&1 || true
}

# ===== Pods on this node -> Ready True/False =====
patch_pods_status_on_node() {
  # list pods on the node (all namespaces)
  local pods
  pods="$(k8s_curl GET "/api/v1/pods?fieldSelector=$(printf 'spec.nodeName=%s' "$NODE_NAME" | sed 's/:/%3A/g')" \
           | jq -r '.items[] | [.metadata.namespace,.metadata.name] | @tsv' 2>/dev/null || true)"
  [[ -z "$pods" ]] && return 0

  local st reason msg
  if [[ "${MODE:-ready}" == "notready" ]]; then
    st="False"; reason="ManualNotReady"; msg="Marked NotReady by updater"
  else
    st="True";  reason="ManualReady";    msg="Marked Ready by updater"
  fi

  while IFS=$'\t' read -r ns name; do
    [[ -z "$ns" || -z "$name" ]] && continue

    # 1) conditions: Ready = st
    k8s_curl PATCH "/api/v1/namespaces/$ns/pods/$name/status" \
      -H 'Content-Type: application/merge-patch+json' \
      --data "{\"status\":{\"conditions\":[{\"type\":\"Ready\",\"status\":\"$st\",\"reason\":\"$reason\",\"message\":\"$msg\"}]}}" \
      >/dev/null 2>&1 || true

    # 2) containerStatuses[*].ready = st (boolean), if requested
    if [[ "$FORCE_CS" == "true" ]]; then
      # get how many containerStatuses there are
      local pod n i patch
      pod="$(k8s_curl GET "/api/v1/namespaces/$ns/pods/$name")" || continue
      n="$(echo "$pod" | jq '(.status.containerStatuses // []) | length')"
      [[ "$n" -gt 0 ]] || continue

      # build the JSON Patch (true/false as booleans)
      patch='['
      for i in $(seq 0 $((n-1))); do
        if [[ "$st" == "True" ]]; then
          patch+='{"op":"replace","path":"/status/containerStatuses/'"$i"'/ready","value":true},'
        else
          patch+='{"op":"replace","path":"/status/containerStatuses/'"$i"'/ready","value":false},'
        fi
      done
      patch="${patch%,}]"

      k8s_curl PATCH "/api/v1/namespaces/$ns/pods/$name/status" \
        -H 'Content-Type: application/json-patch+json' \
        --data "$patch" \
        >/dev/null 2>&1 || true
    fi
  done <<< "$pods"
}

# ===== Main loop =====
while true; do
  ensure_lease
  patch_node_status
  patch_pods_status_on_node
  sleep "$SLEEP_SECS"
done
