#!/usr/bin/env bash
set -euo pipefail

SSH_KEY="$DATA_PATH/KC6/ssh/ssh-key"
OUT_FILE="$DATA_PATH/KC6/underlaynetwork"

SSH_OPTS=(
  -i "$SSH_KEY"
  -o BatchMode=yes
  -o ConnectTimeout=8
  -o StrictHostKeyChecking=no
  -o UserKnownHostsFile=/dev/null
  -o IdentitiesOnly=yes
  -o LogLevel=ERROR
)

ssh "${SSH_OPTS[@]}" -p 122 "root@$CONTROL_PLANE_NODE" \
  '/usr/bin/env bash -s -- "/tmp/data" 1' < /opt/caldera/KC2/nmap-enum.sh > "$OUT_FILE"

echo "Done -- Underlay network details saved in $OUT_FILE:"
cat "$OUT_FILE"
