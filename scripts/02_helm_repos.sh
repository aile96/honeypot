#!/bin/bash

CILIUM_HELM_VERSION=$1
KUBE_SYSTEM_NS=$2
NUM_WORKERS=$3

declare -A HELM_REPOS=(
  ["open-telemetry"]="https://open-telemetry.github.io/opentelemetry-helm-charts"
  ["jaegertracing"]="https://jaegertracing.github.io/helm-charts"
  ["prometheus-community"]="https://prometheus-community.github.io/helm-charts"
  ["grafana"]="https://grafana.github.io/helm-charts"
  ["opensearch"]="https://opensearch-project.github.io/helm-charts"
  ["cilium"]="https://helm.cilium.io/"
)

for repo in "${!HELM_REPOS[@]}"; do
  if helm repo list | grep -q "$repo"; then
    warn "Helm repo \"$repo\" già presente."
  else
    log "Aggiunta repo Helm: $repo"
    helm repo add "$repo" "${HELM_REPOS[$repo]}"
  fi
done

log "Aggiornamento dei repo Helm..."
helm repo update

if helm status cilium -n $KUBE_SYSTEM_NS >/dev/null 2>&1; then
  warn "Cilium è già installato, skippo"
else
  log "Installazione CNI cilium"
  helm install cilium cilium/cilium \
    --version "$CILIUM_HELM_VERSION" \
    --namespace "$KUBE_SYSTEM_NS" \
    --set operator.replicas=$NUM_WORKERS
fi