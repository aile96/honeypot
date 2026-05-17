#!/usr/bin/env python3
"""Python entrypoint for dos-loop.sh."""

from __future__ import annotations

import os
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

_SCRIPT = '#!/usr/bin/env bash\nset -euo pipefail\n\nINTERVAL="3"\nREMOTE_CMD_RAW="${1:-}"\n[[ -n "${REMOTE_CMD_RAW}" ]] || { echo "Usage: $0 \'<remote command prefix>\'" >&2; exit 1; }\n\nread -r -a REMOTE_CMD <<< "${REMOTE_CMD_RAW}"\nCRICTL_BASE=( "${REMOTE_CMD[@]}" crictl )\nCRICTL=()\nKUBELET_PIDS=()\nPROTECTED_CONTAINERS=(\n  "agent"\n  "opensearch"\n  "grafana"\n  "otel-collector"\n  "jaeger"\n  "prometheus"\n)\nRUNTIME_ENDPOINT_CANDIDATES=(\n  "unix:///host/run/cri-dockerd.sock"\n  "unix:///host/var/run/cri-dockerd.sock"\n  "unix:///host/run/containerd/containerd.sock"\n  "unix:///host/var/run/containerd/containerd.sock"\n  "unix:///run/cri-dockerd.sock"\n  "unix:///run/containerd/containerd.sock"\n)\n\ndetect_runtime_endpoint() {\n  local endpoint=""\n\n  for endpoint in "${RUNTIME_ENDPOINT_CANDIDATES[@]}"; do\n    if "${CRICTL_BASE[@]}" --runtime-endpoint "${endpoint}" info >/dev/null 2>&1; then\n      printf \'%s\\n\' "${endpoint}"\n      return 0\n    fi\n  done\n\n  return 1\n}\n\ncleanup() {\n  if (( ${#KUBELET_PIDS[@]} > 0 )); then\n    "${REMOTE_CMD[@]}" kill -CONT "${KUBELET_PIDS[@]}" >/dev/null 2>&1 || true\n  fi\n}\ntrap cleanup EXIT INT TERM\n\nmapfile -t KUBELET_PIDS < <(\n  "${REMOTE_CMD[@]}" pidof kubelet 2>/dev/null \\\n    | tr \' \' \'\\n\' \\\n    | sed \'/^$/d\'\n)\n\nif (( ${#KUBELET_PIDS[@]} == 0 )); then\n  echo "No kubelet PID found"\n  exit 1\nfi\n\nRUNTIME_ENDPOINT="$(detect_runtime_endpoint)" || {\n  echo "No working CRI endpoint found"\n  exit 1\n}\nCRICTL=( "${CRICTL_BASE[@]}" --runtime-endpoint "${RUNTIME_ENDPOINT}" )\necho "Using CRI endpoint: ${RUNTIME_ENDPOINT}"\n\n"${REMOTE_CMD[@]}" kill -STOP "${KUBELET_PIDS[@]}"\n\nwhile :; do\n  # Pod list (sandbox) on node\n  mapfile -t PODS < <("${CRICTL[@]}" pods -q)\n\n  if (( ${#PODS[@]} == 0 )); then\n    echo "No pod found."\n    exit 0\n  fi\n\n  for POD in "${PODS[@]}"; do\n    # Finding containers belonging to pod\n    mapfile -t CIDS < <("${CRICTL[@]}" ps -q --pod "$POD")\n    FILTERED=()\n    for CID in "${CIDS[@]}"; do\n      CONTAINER_NAME=$("${CRICTL[@]}" inspect "$CID" 2>/dev/null | jq -r \'.status.metadata.name // empty\')\n      if printf \'%s\\n\' "${PROTECTED_CONTAINERS[@]}" | grep -Fxq "$CONTAINER_NAME"; then\n        continue\n      fi\n      FILTERED+=("$CID")\n    done\n\n    # If there are no containers in the pod, skip\n    if (( ${#FILTERED[@]} == 0 )); then\n      continue\n    fi\n\n    echo "Pod ${POD}: stopping ${#FILTERED[@]} container..."\n    for CID in "${FILTERED[@]}"; do\n      if ! "${CRICTL[@]}" stop "$CID"; then\n        echo "WARN: stop failed for container $CID (continuing)."\n      fi\n    done\n\n    echo "Pod ${POD}: done"\n  done\n\n  echo "Every pod stopped"\n  sleep "$INTERVAL"\ndone\n'


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
