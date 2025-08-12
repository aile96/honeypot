#!/bin/bash
set -e

echo "Applying Kubernetes manifests to namespace 'demo'..."

# Create namespace
kubectl apply -f namespace.yaml

# Create ConfigMap
kubectl apply -f configmap.yaml -n demo

# Create PVCs first (if any)
for pvc in *-pvc.yaml; do
  [ -e "$pvc" ] || continue
  kubectl apply -f "$pvc" -n demo
done

# Deploy all deployments and services
for file in *-deployment.yaml *-service.yaml; do
  [ -e "$file" ] || continue
  kubectl apply -f "$file" -n demo
done

# Apply ingress last (optional)
if [ -f frontend-ingress.yaml ]; then
  kubectl apply -f frontend-ingress.yaml -n demo
fi

echo "All manifests applied successfully."
