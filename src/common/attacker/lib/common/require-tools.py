#!/usr/bin/env python3
"""Python entrypoint for require-tools.sh."""

from __future__ import annotations

import os
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

_SCRIPT = '#!/usr/bin/env bash\n\nrequire_tools() {\n  local missing=()\n  local tool\n\n  for tool in "$@"; do\n    if ! command -v "$tool" >/dev/null 2>&1; then\n      missing+=("$tool")\n    fi\n  done\n\n  if (( ${#missing[@]} > 0 )); then\n    echo "Missing required tools: ${missing[*]}" >&2\n    echo "Rebuild the attacker image so the preinstalled dependencies are available." >&2\n    exit 1\n  fi\n}\n'


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
