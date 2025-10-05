KEY_PATH="$DATA_PATH/KC5/ssh/ssh-key"
SSH="ssh -p 2222 -i $KEY_PATH user@kind-cluster-worker"
NS="mem"
DEPLOY="kind-cluster-worker"
PVC_NAME="pvc-smb-dyn"
MOUNT_PATH="/pvc"
KCFG="/etc/kubernetes/admin.conf"

# (facoltativo) Anteprima sul server: nessuna modifica
$SSH "KUBECONFIG=$KCFG kubectl -n $NS set volume deployment/$DEPLOY \
  --add --name=$PVC_NAME --type=pvc --claim=$PVC_NAME \
  --mount-path $MOUNT_PATH ${CONTAINER:+--containers $CONTAINER} \
  --dry-run=server -o yaml" | less

# Applica davvero sul server
$SSH "KUBECONFIG=$KCFG kubectl -n $NS set volume deployment/$DEPLOY \
  --add --name=$PVC_NAME --type=pvc --claim=$PVC_NAME \
  --mount-path $MOUNT_PATH ${CONTAINER:+--containers $CONTAINER}"
