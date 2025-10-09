
#!/usr/bin/env bash
set -eu pipefail

SSH_KEY="$DATA_PATH/KC6/ssh/ssh-key"
OUT_FILE="$DATA_PATH/KC6/logenum"

ssh -i "$SSH_KEY" -p 122 root@kind-cluster-control-plane '/usr/bin/env bash -s -- "/tmp"' < /opt/caldera/KC2/pass-enum.sh > $OUT_FILE