#!/usr/bin/env bash
set -euo pipefail

OUT_FILE="${DATA_PATH:-/tmp/KCData}/KC6/logenum"
mkdir -p "$(dirname "${OUT_FILE}")"

# The local registry is reachable from the attacker container, while Kind nodes
# do not always ship Python. Run the enumerator locally and persist the same
# /tmp/user and /tmp/pass artifacts consumed by registry-mod.py.
/opt/caldera/KC2/pass-enum.py \
  /tmp \
  "${REGISTRY_USER:-}" \
  "${REGISTRY_PASS:-}" \
  "${REGISTRY_NAME:-registry}" \
  "${REGISTRY_PORT:-5000}" \
  > "${OUT_FILE}" 2>&1

if [[ -s /tmp/user && -s /tmp/pass ]]; then
  echo "[KC6-613] registry credentials validated; detailed log saved in ${OUT_FILE}"
else
  echo "[KC6-613] registry credential validation did not produce credential files; see ${OUT_FILE}" >&2
  exit 1
fi
