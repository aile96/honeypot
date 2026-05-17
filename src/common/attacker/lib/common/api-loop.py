#!/usr/bin/env python3
"""Python entrypoint for api-loop.sh."""

from __future__ import annotations

import os
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

_SCRIPT = '#!/usr/bin/env bash\nset -euo pipefail\n\n# ===== Config =====\nAPI_SERVER="${API_SERVER:-https://$CONTROL_PLANE_NODE:$CONTROL_PLANE_PORT}"\nCERT_PATH="${CERT_PATH:-/tmp/KCData/KC5/kubelet-client-current-kind-cluster-worker.pem}"\nCACERT_OPT="${CACERT_OPT:---insecure}"           # use --cacert /path/ca.crt if you prefer\nMODE="${MODE:-ready}"                             # ready | notready\nSLEEP_SECS="${SLEEP_SECS:-1}"\nFORCE_CS="${FORCE_CS:-true}"\n\n# ===== Derive NODE_NAME from the certificate CN =====\nNODE_NAME="$(openssl x509 -in "$CERT_PATH" -noout -subject \\\n  | sed -n \'s/^subject=.*CN *= *system:node:\\([^,/]*\\).*/\\1/p\' | head -n1)"\nif [[ -z "$NODE_NAME" ]]; then\n  echo "ERROR: unable to extract NODE_NAME from certificate CN ($CERT_PATH)" >&2\n  exit 1\nfi\n\necho "API: $API_SERVER"\necho "NODE: $NODE_NAME"\necho "MODE: $MODE"\n\n# ===== Helpers =====\nk8s_curl() {\n  # usage: k8s_curl METHOD PATH [curl-args...]\n  local method="$1"; shift\n  local path="$1"; shift\n  curl --silent --show-error --retry 2 --retry-connrefused \\\n    -X "$method" \\\n    "$API_SERVER$path" \\\n    --cert "$CERT_PATH" --key "$CERT_PATH" $CACERT_OPT \\\n    -H \'Accept: application/json\' \\\n    "$@"\n}\n\nnow_rfc3339_ns() { date -u +"%Y-%m-%dT%H:%M:%S.%NZ"; }\nnow_rfc3339()    { date -u +"%Y-%m-%dT%H:%M:%SZ";   }\n\n# ===== Lease: create/update =====\nensure_lease() {\n  local now; now="$(now_rfc3339_ns)"\n  # try to GET the lease\n  local body; body="$(k8s_curl GET "/apis/coordination.k8s.io/v1/namespaces/kube-node-lease/leases/$NODE_NAME")" || true\n  if echo "$body" | jq -e \'.metadata.name\' >/dev/null 2>&1; then\n    # PATCH merge renewTime/holderIdentity\n    k8s_curl PATCH "/apis/coordination.k8s.io/v1/namespaces/kube-node-lease/leases/$NODE_NAME" \\\n      -H \'Content-Type: application/merge-patch+json\' \\\n      --data "{\\"spec\\":{\\"renewTime\\":\\"$now\\",\\"holderIdentity\\":\\"$NODE_NAME\\"}}" >/dev/null 2>&1 || true\n  else\n    # CREATE (POST on the collection)\n    k8s_curl POST "/apis/coordination.k8s.io/v1/namespaces/kube-node-lease/leases" \\\n      -H \'Content-Type: application/json\' \\\n      --data @<(cat <<JSON\n{"apiVersion":"coordination.k8s.io/v1","kind":"Lease",\n "metadata":{"name":"$NODE_NAME","namespace":"kube-node-lease"},\n "spec":{"holderIdentity":"$NODE_NAME","leaseDurationSeconds":40,"renewTime":"$now","leaseTransitions":0}}\nJSON\n) >/dev/null 2>&1 || true\n  fi\n}\n\n# ===== Node.status Ready True/False =====\npatch_node_status() {\n  local st reason msg; local t1 t2\n  if [[ "$MODE" == "notready" ]]; then\n    st="False"; reason="ManualNotReady"; msg="Marked NotReady by updater"\n  else\n    st="True";  reason="ManualReady";    msg="Marked Ready by updater"\n  fi\n  t1="$(now_rfc3339)"; t2="$t1"\n  k8s_curl PATCH "/api/v1/nodes/$NODE_NAME/status" \\\n    -H \'Content-Type: application/strategic-merge-patch+json\' \\\n    --data @<(cat <<JSON\n{"status":{"conditions":[\n  {"type":"Ready","status":"$st","reason":"$reason","message":"$msg",\n   "lastHeartbeatTime":"$t1","lastTransitionTime":"$t2"}\n]}}\nJSON\n) >/dev/null 2>&1 || true\n}\n\n# ===== Pods on this node -> Ready True/False =====\npatch_pods_status_on_node() {\n  # list pods on the node (all namespaces)\n  local pods\n  pods="$(k8s_curl GET "/api/v1/pods?fieldSelector=$(printf \'spec.nodeName=%s\' "$NODE_NAME" | sed \'s/:/%3A/g\')" \\\n           | jq -r \'.items[] | [.metadata.namespace,.metadata.name] | @tsv\' 2>/dev/null || true)"\n  [[ -z "$pods" ]] && return 0\n\n  local st reason msg\n  if [[ "${MODE:-ready}" == "notready" ]]; then\n    st="False"; reason="ManualNotReady"; msg="Marked NotReady by updater"\n  else\n    st="True";  reason="ManualReady";    msg="Marked Ready by updater"\n  fi\n\n  while IFS=$\'\\t\' read -r ns name; do\n    [[ -z "$ns" || -z "$name" ]] && continue\n\n    # 1) conditions: Ready = st\n    k8s_curl PATCH "/api/v1/namespaces/$ns/pods/$name/status" \\\n      -H \'Content-Type: application/merge-patch+json\' \\\n      --data "{\\"status\\":{\\"conditions\\":[{\\"type\\":\\"Ready\\",\\"status\\":\\"$st\\",\\"reason\\":\\"$reason\\",\\"message\\":\\"$msg\\"}]}}" \\\n      >/dev/null 2>&1 || true\n\n    # 2) containerStatuses[*].ready = st (boolean), if requested\n    if [[ "$FORCE_CS" == "true" ]]; then\n      # get how many containerStatuses there are\n      local pod n i patch\n      pod="$(k8s_curl GET "/api/v1/namespaces/$ns/pods/$name")" || continue\n      n="$(echo "$pod" | jq \'(.status.containerStatuses // []) | length\')"\n      [[ "$n" -gt 0 ]] || continue\n\n      # build the JSON Patch (true/false as booleans)\n      patch=\'[\'\n      for i in $(seq 0 $((n-1))); do\n        if [[ "$st" == "True" ]]; then\n          patch+=\'{"op":"replace","path":"/status/containerStatuses/\'"$i"\'/ready","value":true},\'\n        else\n          patch+=\'{"op":"replace","path":"/status/containerStatuses/\'"$i"\'/ready","value":false},\'\n        fi\n      done\n      patch="${patch%,}]"\n\n      k8s_curl PATCH "/api/v1/namespaces/$ns/pods/$name/status" \\\n        -H \'Content-Type: application/json-patch+json\' \\\n        --data "$patch" \\\n        >/dev/null 2>&1 || true\n    fi\n  done <<< "$pods"\n}\n\n# ===== Main loop =====\nwhile true; do\n  ensure_lease\n  patch_node_status\n  patch_pods_status_on_node\n  sleep "$SLEEP_SECS"\ndone\n'


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
