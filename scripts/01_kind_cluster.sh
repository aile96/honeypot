#!/bin/bash

CLUSTER_NAME=$1
NUM_WORKERS=$2
REGISTRY_NAME=$3
REGISTRY_PORT=$4
REGISTRY_USER=$5
REGISTRY_PASS=$6
REGISTRY_MALICIOUS_NAME=$7
CILIUM_HELM_VERSION=$8
KUBE_SYSTEM_NS=$9

deploy_metallb() {
  log "Controllo presenza MetalLB e pool IP..."

  # Controlla se MetalLB è già installato
  if kubectl get ns metallb-system &>/dev/null; then
    if helm list -n metallb-system | grep -q metallb; then
      warn "MetalLB già installato via Helm, salto installazione."
    else
      warn "MetalLB namespace presente ma non gestito da Helm. Installo con Helm..."
      helm upgrade --install metallb metallb/metallb -n metallb-system --create-namespace
    fi
  else
    log "MetalLB non trovato. Installo con Helm..."
    helm upgrade --install metallb metallb/metallb -n metallb-system --create-namespace
  fi

  log "Aspetto che MetalLB sia pronto (controller + webhook)..."

  # Attendi controller Available
  kubectl -n metallb-system wait --for=condition=Available deploy/metallb-controller --timeout=180s

  # Attendi speaker (DaemonSet) che abbia almeno 1 pod Ready
  kubectl -n metallb-system rollout status ds/metallb-speaker --timeout=180s

  # Attendi che il service del webhook punti a pod pronti (endpoint popolati)
  log "Verifico endpoint del metallb-webhook-service…"
  for i in {1..18}; do  # ~90s di retry
    if kubectl -n metallb-system get endpoints metallb-webhook-service -o jsonpath='{.subsets[0].addresses[0].ip}' >/dev/null 2>&1; then
      log "Webhook pronto (endpoint presenti)."
      break
    fi
    warn "Endpoint non ancora presenti, retry $i/18…"
    sleep 5
  done

  # (opzionale) fail esplicito se dopo i retry non ci sono endpoint
  if ! kubectl -n metallb-system get endpoints metallb-webhook-service -o jsonpath='{.subsets[0].addresses[0].ip}' >/dev/null 2>&1; then
    die "metallb-webhook-service senza endpoint: controlla i pod/metallb-controller."
  fi
  
  log "MetalLB pronto"

  # Controlla se esiste già un IPAddressPool "default-pool"
  if kubectl get ipaddresspool -n metallb-system default-pool &>/dev/null; then
    log "IPAddressPool 'default-pool' già esistente, nessuna creazione necessaria."
  else
    log "Creazione IPAddressPool 'default-pool'..."
    kubectl apply -f - <<'EOF'
apiVersion: metallb.io/v1beta1
kind: IPAddressPool
metadata:
  namespace: metallb-system
  name: default-pool
spec:
  addresses:
  - 172.18.0.200-172.18.0.250
---
apiVersion: metallb.io/v1beta1
kind: L2Advertisement
metadata:
  namespace: metallb-system
  name: adv
spec:
  ipAddressPools:
  - default-pool
EOF
  fi
}

create_kind_cluster() {
  log "Creazione cluster Kind \"$CLUSTER_NAME\" con $NUM_WORKERS worker(s)..."

  # Costruzione dinamica dei nodi worker
  WORKER_NODES=""
  for ((i = 0; i < NUM_WORKERS; i++)); do
  WORKER_NODES+="
  - role: worker
    kubeadmConfigPatches:
    - |
      kind: JoinConfiguration
      nodeRegistration:
        kubeletExtraArgs:
          read-only-port: \"10255\"
          address: \"0.0.0.0\"
"
  done

  cat <<EOF | kind create cluster --name "$CLUSTER_NAME" --config=-
kind: Cluster
apiVersion: kind.x-k8s.io/v1alpha4
networking:
  disableDefaultCNI: true
  podSubnet: "10.244.0.0/16"
  serviceSubnet: "10.96.0.0/12"
containerdConfigPatches:
  - |
    [plugins."io.containerd.grpc.v1.cri".registry]
      [plugins."io.containerd.grpc.v1.cri".registry.mirrors."${REGISTRY_NAME}:${REGISTRY_PORT}"]
        endpoint = ["https://${REGISTRY_NAME}:${REGISTRY_PORT}"]
      [plugins."io.containerd.grpc.v1.cri".registry.configs."${REGISTRY_NAME}:${REGISTRY_PORT}".auth]
        username = "${REGISTRY_USER}"
        password = "${REGISTRY_PASS}"
      [plugins."io.containerd.grpc.v1.cri".registry.configs."${REGISTRY_NAME}:${REGISTRY_PORT}".tls]
        insecure_skip_verify = true
      [plugins."io.containerd.grpc.v1.cri".registry.mirrors."${REGISTRY_MALICIOUS_NAME}:${REGISTRY_PORT}"]
        endpoint = ["https://${REGISTRY_NAME}:${REGISTRY_PORT}"]
      [plugins."io.containerd.grpc.v1.cri".registry.configs."${REGISTRY_MALICIOUS_NAME}:${REGISTRY_PORT}".auth]
        username = "${REGISTRY_USER}"
        password = "${REGISTRY_PASS}"
      [plugins."io.containerd.grpc.v1.cri".registry.configs."${REGISTRY_MALICIOUS_NAME}:${REGISTRY_PORT}".tls]
        insecure_skip_verify = true
nodes:
  - role: control-plane
    extraPortMappings:
      - containerPort: ${REGISTRY_PORT}
    extraMounts:
      - hostPath: $(pwd)/pb/certs
        containerPath: /certs
$WORKER_NODES
EOF
}

if kind get clusters | grep -q "^$CLUSTER_NAME$"; then
  warn "Cluster \"$CLUSTER_NAME\" già esistente, nessuna creazione necessaria."
else
  create_kind_cluster
fi

if helm status cilium -n $KUBE_SYSTEM_NS >/dev/null 2>&1; then
  warn "Cilium è già installato, skippo"
else
  log "Installazione CNI cilium"
  helm install cilium cilium/cilium \
    --version "$CILIUM_HELM_VERSION" \
    --namespace "$KUBE_SYSTEM_NS" \
    --set operator.replicas=$NUM_WORKERS
fi

deploy_metallb