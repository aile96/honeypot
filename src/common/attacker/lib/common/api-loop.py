#!/usr/bin/env python3
"""Python entrypoint for api-loop.sh."""

from __future__ import annotations

import os
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

_SCRIPT = r"""#!/usr/bin/env bash
set -euo pipefail

# ==============================================================================
# Config
# ==============================================================================

API_SERVER="${API_SERVER:-https://$CONTROL_PLANE_NODE:$CONTROL_PLANE_PORT}"
CERT_PATH="${CERT_PATH:-/tmp/KCData/KC5/kubelet-client-current-kind-cluster-worker.pem}"
CACERT_OPT="${CACERT_OPT:---insecure}"   # Use: --cacert /path/ca.crt if preferred
MODE="${MODE:-ready}"                    # ready | notready
SLEEP_SECS="${SLEEP_SECS:-1}"
FORCE_CS="${FORCE_CS:-true}"

# ==============================================================================
# Node name
# ==============================================================================

get_node_name_from_cert() {
  openssl x509 -in "$CERT_PATH" -noout -subject \
    | sed -n 's/^subject=.*CN *= *system:node:\([^,/]*\).*/\1/p' \
    | head -n1
}

NODE_NAME="$(get_node_name_from_cert)"

if [[ -z "$NODE_NAME" ]]; then
  echo "ERROR: unable to extract NODE_NAME from certificate CN ($CERT_PATH)" >&2
  exit 1
fi

echo "API: $API_SERVER"
echo "NODE: $NODE_NAME"
echo "MODE: $MODE"

# ==============================================================================
# Helpers
# ==============================================================================

k8s_curl() {
  # Usage:
  #   k8s_curl METHOD PATH [curl-args...]

  local method="$1"
  local path="$2"

  shift 2

  curl \
    --silent \
    --show-error \
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

now_rfc3339_ns() {
  date -u +"%Y-%m-%dT%H:%M:%S.%NZ"
}

now_rfc3339() {
  date -u +"%Y-%m-%dT%H:%M:%SZ"
}

current_status_fields() {
  if [[ "$MODE" == "notready" ]]; then
    STATUS_VALUE="False"
    STATUS_REASON="ManualNotReady"
    STATUS_MESSAGE="Marked NotReady by updater"
  else
    STATUS_VALUE="True"
    STATUS_REASON="ManualReady"
    STATUS_MESSAGE="Marked Ready by updater"
  fi
}

# ==============================================================================
# Lease: create/update
# ==============================================================================

ensure_lease() {
  local now
  local body

  now="$(now_rfc3339_ns)"

  body="$(
    k8s_curl GET "/apis/coordination.k8s.io/v1/namespaces/kube-node-lease/leases/$NODE_NAME"
  )" || true

  if echo "$body" | jq -e ".metadata.name" >/dev/null 2>&1; then
    renew_lease "$now"
  else
    create_lease "$now"
  fi
}

renew_lease() {
  local now="$1"

  k8s_curl PATCH "/apis/coordination.k8s.io/v1/namespaces/kube-node-lease/leases/$NODE_NAME" \
    -H "Content-Type: application/merge-patch+json" \
    --data @- >/dev/null 2>&1 <<JSON || true
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
    --data @- >/dev/null 2>&1 <<JSON || true
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
# Node.status Ready True/False
# ==============================================================================

patch_node_status() {
  local now

  current_status_fields
  now="$(now_rfc3339)"

  k8s_curl PATCH "/api/v1/nodes/$NODE_NAME/status" \
    -H "Content-Type: application/strategic-merge-patch+json" \
    --data @- >/dev/null 2>&1 <<JSON || true
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
# Pods on this node -> Ready True/False
# ==============================================================================

pods_on_node() {
  local field_selector

  field_selector="$(
    printf "spec.nodeName=%s" "$NODE_NAME" \
      | sed "s/:/%3A/g"
  )"

  k8s_curl GET "/api/v1/pods?fieldSelector=$field_selector" \
    | jq -r ".items[] | [.metadata.namespace, .metadata.name] | @tsv" 2>/dev/null || true
}

patch_pods_status_on_node() {
  local pods

  pods="$(pods_on_node)"
  [[ -z "$pods" ]] && return 0

  current_status_fields

  while IFS=$'\t' read -r namespace pod_name; do
    [[ -z "$namespace" || -z "$pod_name" ]] && continue

    patch_pod_ready_condition "$namespace" "$pod_name"

    if [[ "$FORCE_CS" == "true" ]]; then
      patch_pod_container_statuses "$namespace" "$pod_name"
    fi
  done <<< "$pods"
}

patch_pod_ready_condition() {
  local namespace="$1"
  local pod_name="$2"

  k8s_curl PATCH "/api/v1/namespaces/$namespace/pods/$pod_name/status" \
    -H "Content-Type: application/merge-patch+json" \
    --data @- >/dev/null 2>&1 <<JSON || true
{
  "status": {
    "conditions": [
      {
        "type": "Ready",
        "status": "$STATUS_VALUE",
        "reason": "$STATUS_REASON",
        "message": "$STATUS_MESSAGE"
      }
    ]
  }
}
JSON
}

patch_pod_container_statuses() {
  local namespace="$1"
  local pod_name="$2"

  local pod
  local count
  local patch

  pod="$(k8s_curl GET "/api/v1/namespaces/$namespace/pods/$pod_name")" || return 0
  count="$(echo "$pod" | jq "(.status.containerStatuses // []) | length")"

  [[ "$count" -gt 0 ]] || return 0

  patch="$(build_container_status_patch "$count")"

  k8s_curl PATCH "/api/v1/namespaces/$namespace/pods/$pod_name/status" \
    -H "Content-Type: application/json-patch+json" \
    --data "$patch" \
    >/dev/null 2>&1 || true
}

build_container_status_patch() {
  local count="$1"
  local ready_bool="true"

  if [[ "$STATUS_VALUE" != "True" ]]; then
    ready_bool="false"
  fi

  jq -nc --argjson count "$count" --argjson ready "$ready_bool" '
    [
      range(0; $count) as $i
      | {
          op: "replace",
          path: "/status/containerStatuses/\($i)/ready",
          value: $ready
        }
    ]
  '
}

# ==============================================================================
# Main loop
# ==============================================================================

while true; do
  ensure_lease
  patch_node_status
  patch_pods_status_on_node

  sleep "$SLEEP_SECS"
done
"""


def _run_embedded_bash(script: str, argv: list[str]) -> int:
    """Run the embedded legacy payload through Bash with a Python-owned entrypoint."""
    try:
        current = Path(__file__).resolve()
        for parent in current.parents:
            lib_dir = parent / "lib"
            if (lib_dir / "common" / "shell.py").is_file():
                sys.path.insert(0, str(lib_dir))
                break
        from common.shell import run_embedded_bash
        return run_embedded_bash(script, argv)
    except Exception:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".sh", delete=False) as handle:
            handle.write(script)
            path = handle.name
        try:
            Path(path).chmod(Path(path).stat().st_mode | stat.S_IXUSR)
            completed = subprocess.run(["/usr/bin/env", "bash", path, *argv], text=True)
            return int(completed.returncode)
        finally:
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass


def main() -> None:
    raise SystemExit(_run_embedded_bash(_SCRIPT, sys.argv[1:]))


if __name__ == "__main__":
    main()
