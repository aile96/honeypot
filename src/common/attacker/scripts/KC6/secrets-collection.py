#!/usr/bin/env python3
"""Python entrypoint for secrets-collection.sh."""

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
OUTFILE="${OUTFILE:-$DATA_PATH/KC6/secrets}"
mkdir -p "$(dirname -- "$OUTFILE")"

kubectl --kubeconfig "$KUBECONFIG" get secrets --all-namespaces -o json \
| jq -r '
  .items[]
  | {
      ns: .metadata.namespace,
      name: .metadata.name,
      type: .type,
      data: (.data // {})
    }
  | . as $s
  | ($s.data | to_entries[]? | {
      ns: $s.ns, name: $s.name, type: $s.type, key: .key, val: .value
    })
  | [ .ns, .name, .type, .key, (.val | @base64d) ]
  | @tsv
' > "$OUTFILE"

echo "Done: see all the secrets in $OUTFILE"
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
