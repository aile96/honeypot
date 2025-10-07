#!/usr/bin/env bash
set -euo pipefail

KUBECONFIG="${KUBECONFIG:-$DATA_PATH/KC6/ops-admin.kubeconfig}"
CRB_NAME="unauthenticated-admin"

kubectl --kubeconfig $KUBECONFIG delete clusterrolebinding $CRB_NAME --ignore-not-found

if kubectl --kubeconfig $KUBECONFIG get clusterrolebinding $CRB_NAME >/dev/null 2>&1; then
  echo "Error in deleting"
else
  echo "Deletion done"
fi