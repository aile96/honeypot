#!/usr/bin/env python3
"""Python entrypoint for list-node-ips.sh."""

from __future__ import annotations

import os
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

_SCRIPT = r"""#!/usr/bin/env bash

# Read "IP - HOST" entries and return worker node IPs.
# Usage: list_worker_node_ips [iphost_file]
list_worker_node_ips() {
  local file_ip="${1:-/tmp/iphost}"

  if [[ ! -f "${file_ip}" ]]; then
    echo "Error: file '${file_ip}' not found" >&2
    return 1
  fi

  echo ">> Recover IPs list (file: ${file_ip})..." >&2
  awk -F'-' '
    /^[[:space:]]*#/ { next }
    /^[[:space:]]*$/ { next }
    NF >= 2 {
      ip=$1; host=$2
      gsub(/^[ \t]+|[ \t\r]+$/, "", ip)
      gsub(/^[ \t]+|[ \t\r]+$/, "", host)
      if (host ~ /^worker([0-9]+)?$/ && ip ~ /^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$/) {
        print ip
      }
    }
  ' "${file_ip}" | sort -u
}
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
