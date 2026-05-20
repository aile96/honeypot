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

cat "${OUT_FILE}"
