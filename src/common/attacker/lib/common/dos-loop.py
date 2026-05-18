#!/usr/bin/env python3
"""Python entrypoint for dos-loop.sh."""

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

INTERVAL="3"

REMOTE_CMD_RAW="${1:-}"
if [[ -z "$REMOTE_CMD_RAW" ]]; then
  echo "Usage: $0 '<remote command prefix>'" >&2
  exit 1
fi

read -r -a REMOTE_CMD <<< "$REMOTE_CMD_RAW"

CRICTL_BASE=("${REMOTE_CMD[@]}" crictl)
CRICTL=()
KUBELET_PIDS=()

PROTECTED_CONTAINERS=(
  "agent"
  "opensearch"
  "grafana"
  "otel-collector"
  "jaeger"
  "prometheus"
)

RUNTIME_ENDPOINT_CANDIDATES=(
  "unix:///host/run/cri-dockerd.sock"
  "unix:///host/var/run/cri-dockerd.sock"
  "unix:///host/run/containerd/containerd.sock"
  "unix:///host/var/run/containerd/containerd.sock"
  "unix:///run/cri-dockerd.sock"
  "unix:///run/containerd/containerd.sock"
)

# ==============================================================================
# Cleanup
# ==============================================================================

cleanup() {
  if (( ${#KUBELET_PIDS[@]} > 0 )); then
    "${REMOTE_CMD[@]}" kill -CONT "${KUBELET_PIDS[@]}" >/dev/null 2>&1 || true
  fi
}

trap cleanup EXIT INT TERM

# ==============================================================================
# Runtime detection
# ==============================================================================

detect_runtime_endpoint() {
  local endpoint

  for endpoint in "${RUNTIME_ENDPOINT_CANDIDATES[@]}"; do
    if "${CRICTL_BASE[@]}" --runtime-endpoint "$endpoint" info >/dev/null 2>&1; then
      printf "%s\n" "$endpoint"
      return 0
    fi
  done

  return 1
}

init_crictl() {
  local runtime_endpoint

  runtime_endpoint="$(detect_runtime_endpoint)" || {
    echo "No working CRI endpoint found"
    exit 1
  }

  CRICTL=("${CRICTL_BASE[@]}" --runtime-endpoint "$runtime_endpoint")

  echo "Using CRI endpoint: $runtime_endpoint"
}

# ==============================================================================
# Kubelet handling
# ==============================================================================

find_kubelet_pids() {
  mapfile -t KUBELET_PIDS < <(
    "${REMOTE_CMD[@]}" pidof kubelet 2>/dev/null \
      | tr " " "\n" \
      | sed "/^$/d"
  )

  if (( ${#KUBELET_PIDS[@]} == 0 )); then
    echo "No kubelet PID found"
    exit 1
  fi
}

pause_kubelet() {
  "${REMOTE_CMD[@]}" kill -STOP "${KUBELET_PIDS[@]}"
}

# ==============================================================================
# Container filtering
# ==============================================================================

is_protected_container() {
  local container_name="$1"

  printf "%s\n" "${PROTECTED_CONTAINERS[@]}" \
    | grep -Fxq "$container_name"
}

container_name_for_id() {
  local container_id="$1"

  "${CRICTL[@]}" inspect "$container_id" 2>/dev/null \
    | jq -r ".status.metadata.name // empty"
}

containers_for_pod() {
  local pod_id="$1"

  "${CRICTL[@]}" ps -q --pod "$pod_id"
}

filtered_containers_for_pod() {
  local pod_id="$1"
  local container_id
  local container_name

  while IFS= read -r container_id; do
    [[ -z "$container_id" ]] && continue

    container_name="$(container_name_for_id "$container_id")"

    if is_protected_container "$container_name"; then
      continue
    fi

    printf "%s\n" "$container_id"
  done < <(containers_for_pod "$pod_id")
}

# ==============================================================================
# Pod handling
# ==============================================================================

list_pods() {
  "${CRICTL[@]}" pods -q
}

stop_container() {
  local container_id="$1"

  if ! "${CRICTL[@]}" stop "$container_id"; then
    echo "WARN: stop failed for container $container_id (continuing)."
  fi
}

stop_pod_containers() {
  local pod_id="$1"
  local container_id
  local filtered_containers=()

  mapfile -t filtered_containers < <(filtered_containers_for_pod "$pod_id")

  if (( ${#filtered_containers[@]} == 0 )); then
    return 0
  fi

  echo "Pod $pod_id: stopping ${#filtered_containers[@]} container..."

  for container_id in "${filtered_containers[@]}"; do
    stop_container "$container_id"
  done

  echo "Pod $pod_id: done"
}

stop_all_pod_containers_once() {
  local pods=()
  local pod_id

  mapfile -t pods < <(list_pods)

  if (( ${#pods[@]} == 0 )); then
    echo "No pod found."
    exit 0
  fi

  for pod_id in "${pods[@]}"; do
    stop_pod_containers "$pod_id"
  done

  echo "Every pod stopped"
}

# ==============================================================================
# Main
# ==============================================================================

find_kubelet_pids
init_crictl
pause_kubelet

while :; do
  stop_all_pod_containers_once
  sleep "$INTERVAL"
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
