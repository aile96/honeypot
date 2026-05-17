#!/usr/bin/env python3
"""Python entrypoint for undo-RBAC.sh."""

from __future__ import annotations

import os
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

_SCRIPT = r"""#!/usr/bin/env bash
set -euo pipefail

KUBECONFIG="${KUBECONFIG:-$DATA_PATH/KC6/ops-admin.kubeconfig}"
CRB_NAME="unauthenticated-admin"

kubectl --kubeconfig "$KUBECONFIG" delete clusterrolebinding "$CRB_NAME" --ignore-not-found

if kubectl --kubeconfig "$KUBECONFIG" get clusterrolebinding "$CRB_NAME" >/dev/null 2>&1; then
  echo "Error in deleting"
else
  echo "Deletion done"
fi
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
