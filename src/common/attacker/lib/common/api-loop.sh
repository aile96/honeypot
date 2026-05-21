#!/usr/bin/env bash
set -euo pipefail

# ==============================================================================
# Config
# ==============================================================================

API_SERVER="${API_SERVER:-https://${CONTROL_PLANE_NODE}:${CONTROL_PLANE_PORT}}"
CERT_PATH="${CERT_PATH:-/tmp/KCData/KC5/kubelet-client-current-kind-cluster-worker.pem}"
CACERT_OPT="${CACERT_OPT:---insecure}"
MODE="${MODE:-ready}"                    # ready | notready
SLEEP_SECS="${SLEEP_SECS:-1}"
FORCE_CS="${FORCE_CS:-true}"

# ==============================================================================
# Node name
# ==============================================================================

get_node_name_from_cert() {
  openssl x509 -in "$CERT_PATH" -noout -subject 2>/dev/null \
    | sed -n 's/^subject=.*CN *= *system:node:\([^,/]*\).*/\1/p' \
    | head -n1
}

NODE_NAME="$(get_node_name_from_cert)"

if [[ -z "$NODE_NAME" ]]; then
  echo "ERROR: unable to extract NODE_NAME from certificate CN ($CERT_PATH)" >&2
  exit 1
fi

if ! grep -q "BEGIN .*PRIVATE KEY" "$CERT_PATH"; then
  echo "ERROR: certificate file does not contain a private key: $CERT_PATH" >&2
  exit 1
fi

echo "API: $API_SERVER"
echo "NODE: $NODE_NAME"
echo "MODE: $MODE"
echo "CERT: $CERT_PATH"

# ==============================================================================
# Helpers
# ==============================================================================

k8s_curl() {
  local method="$1"
  local path="$2"

  shift 2

  curl \
    --silent \
    --show-error \
    --fail \
    --retry 2 \
    --retry-connrefused \
    -X "$method" \
    "$API_SERVER$path" \
    --cert "$CERT_PATH" \
    --key "$CERT_PATH" \
    $CACERT_OPT \
    -H "Accept: application/json" \
    "$@"
}

urlencode() {
  printf "%s" "$1" | jq -sRr @uri
}

now_rfc3339_ns() {
  date -u +"%Y-%m-%dT%H:%M:%S.%NZ"
}

now_rfc3339() {
  date -u +"%Y-%m-%dT%H:%M:%SZ"
}

current_status_fields() {
  if [[ "$MODE" == "notready" ]]; then
    STATUS_VALUE="False"
    READY_BOOL="false"
    PHASE_VALUE="Running"
    STATUS_REASON="ManualNotReady"
    STATUS_MESSAGE="Marked NotReady by false state updater"
    CONTAINER_STATE_REASON="ManualNotReady"
    CONTAINER_STATE_MESSAGE="Marked NotReady by false state updater"
  else
    STATUS_VALUE="True"
    READY_BOOL="true"
    PHASE_VALUE="Running"
    STATUS_REASON="ManualReady"
    STATUS_MESSAGE="Marked Ready by false state updater"
    CONTAINER_STATE_REASON=""
    CONTAINER_STATE_MESSAGE=""
  fi
}

# ==============================================================================
# Preflight
# ==============================================================================

preflight() {
  echo "Preflight: checking API access"

  k8s_curl GET "/api/v1/nodes/${NODE_NAME}" >/dev/null

  echo "Preflight OK: node ${NODE_NAME} is reachable with provided certificate"
}

# ==============================================================================
# Lease handling
# ==============================================================================

ensure_lease() {
  local now
  local body

  now="$(now_rfc3339_ns)"

  if body="$(k8s_curl GET "/apis/coordination.k8s.io/v1/namespaces/kube-node-lease/leases/${NODE_NAME}" 2>/dev/null)"; then
    if echo "$body" | jq -e ".metadata.name" >/dev/null 2>&1; then
      renew_lease "$now"
      return 0
    fi
  fi

  create_lease "$now"
}

renew_lease() {
  local now="$1"

  k8s_curl PATCH "/apis/coordination.k8s.io/v1/namespaces/kube-node-lease/leases/${NODE_NAME}" \
    -H "Content-Type: application/merge-patch+json" \
    --data @- >/dev/null <<JSON
{
  "spec": {
    "renewTime": "$now",
    "holderIdentity": "$NODE_NAME"
  }
}
JSON
}

create_lease() {
  local now="$1"

  k8s_curl POST "/apis/coordination.k8s.io/v1/namespaces/kube-node-lease/leases" \
    -H "Content-Type: application/json" \
    --data @- >/dev/null <<JSON
{
  "apiVersion": "coordination.k8s.io/v1",
  "kind": "Lease",
  "metadata": {
    "name": "$NODE_NAME",
    "namespace": "kube-node-lease"
  },
  "spec": {
    "holderIdentity": "$NODE_NAME",
    "leaseDurationSeconds": 40,
    "renewTime": "$now",
    "leaseTransitions": 0
  }
}
JSON
}

# ==============================================================================
# Node status
# ==============================================================================

patch_node_status() {
  local now

  current_status_fields
  now="$(now_rfc3339)"

  k8s_curl PATCH "/api/v1/nodes/${NODE_NAME}/status" \
    -H "Content-Type: application/strategic-merge-patch+json" \
    --data @- >/dev/null <<JSON
{
  "status": {
    "conditions": [
      {
        "type": "Ready",
        "status": "$STATUS_VALUE",
        "reason": "$STATUS_REASON",
        "message": "$STATUS_MESSAGE",
        "lastHeartbeatTime": "$now",
        "lastTransitionTime": "$now"
      }
    ]
  }
}
JSON
}

# ==============================================================================
# Pod status
# ==============================================================================

pods_on_node() {
  local selector
  local selector_encoded

  selector="spec.nodeName=${NODE_NAME}"
  selector_encoded="$(urlencode "$selector")"

  k8s_curl GET "/api/v1/pods?fieldSelector=${selector_encoded}" \
    | jq -r ".items[] | [.metadata.namespace, .metadata.name] | @tsv"
}

build_pod_status_patch() {
  local pod_json="$1"
  local now="$2"

  current_status_fields

  jq -nc \
    --argjson pod "$pod_json" \
    --arg now "$now" \
    --arg status_value "$STATUS_VALUE" \
    --argjson ready_bool "$READY_BOOL" \
    --arg phase_value "$PHASE_VALUE" \
    --arg reason "$STATUS_REASON" \
    --arg message "$STATUS_MESSAGE" '
      def forced_condition($t):
        {
          type: $t,
          status: $status_value,
          reason: $reason,
          message: $message,
          lastProbeTime: null,
          lastTransitionTime: $now
        };

      def normalize_condition:
        if .type == "Ready" then forced_condition("Ready")
        elif .type == "ContainersReady" then forced_condition("ContainersReady")
        elif .type == "Initialized" then
          .status = "True"
        elif .type == "PodScheduled" then
          .status = "True"
        else
          .
        end;

      def fake_container_status:
        .ready = $ready_bool
        | .started = $ready_bool
        | .restartCount = 0
        | if $ready_bool then
            .state = {
              running: {
                startedAt: $now
              }
            }
          else
            .state = {
              waiting: {
                reason: "ManualNotReady",
                message: "Marked NotReady by false state updater"
              }
            }
          end
        | .lastState = {};

      {
        status: {
          phase: $phase_value,
          reason: null,
          message: null,
          conditions: (
            ($pod.status.conditions // [])
            | map(normalize_condition)
          ),
          containerStatuses: (
            ($pod.status.containerStatuses // [])
            | map(fake_container_status)
          ),
          initContainerStatuses: (
            ($pod.status.initContainerStatuses // [])
            | map(fake_container_status)
          )
        }
      }
    '
}

patch_pod_status() {
  local namespace="$1"
  local pod_name="$2"
  local pod_json
  local patch
  local now

  pod_json="$(k8s_curl GET "/api/v1/namespaces/${namespace}/pods/${pod_name}")"
  now="$(now_rfc3339)"

  patch="$(build_pod_status_patch "$pod_json" "$now")"

  k8s_curl PATCH "/api/v1/namespaces/${namespace}/pods/${pod_name}/status" \
    -H "Content-Type: application/merge-patch+json" \
    --data "$patch" >/dev/null
}

patch_pods_status_on_node() {
  local pods
  local namespace
  local pod_name
  local patched=0
  local failed=0

  pods="$(pods_on_node || true)"

  if [[ -z "$pods" ]]; then
    echo "No pods found on node ${NODE_NAME}"
    return 0
  fi

  while IFS=$'\t' read -r namespace pod_name; do
    [[ -z "${namespace:-}" || -z "${pod_name:-}" ]] && continue

    if patch_pod_status "$namespace" "$pod_name"; then
      patched=$((patched + 1))
    else
      failed=$((failed + 1))
      echo "WARN: failed to patch pod status ${namespace}/${pod_name}" >&2
    fi
  done <<< "$pods"

  echo "Patched pod statuses on ${NODE_NAME}: patched=${patched}, failed=${failed}"

  if [[ "$patched" -eq 0 ]]; then
    return 1
  fi

  return 0
}

# ==============================================================================
# Main loop
# ==============================================================================

preflight

iteration=0

while true; do
  iteration=$((iteration + 1))

  echo "Iteration ${iteration}: updating false status for node ${NODE_NAME}"

  ensure_lease || echo "WARN: lease update failed for ${NODE_NAME}" >&2
  patch_node_status || echo "WARN: node status patch failed for ${NODE_NAME}" >&2
  patch_pods_status_on_node || echo "WARN: pod status patch failed for ${NODE_NAME}" >&2

  sleep "$SLEEP_SECS"
done