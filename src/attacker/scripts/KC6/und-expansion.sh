
#!/usr/bin/env bash
set -eu pipefail

SSH_KEY="$DATA_PATH/KC6/ssh/ssh-key"
OUT_FILE="$DATA_PATH/KC6/underlaynetwork"

ssh -i "$SSH_KEY" -p 122 root@kind-cluster-control-plane '/usr/bin/env bash -s -- "/tmp/data" 1' < /opt/caldera/KC2/nmap-enum.sh > $OUT_FILE