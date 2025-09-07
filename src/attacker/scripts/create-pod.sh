#!/bin/sh

# Percorsi standard montati nel Pod
TOKEN_PATH="/token"
CA_PATH="/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"
NAMESPACE_PATH="/var/run/secrets/kubernetes.io/serviceaccount/namespace"

# Leggo i dati dal filesystem
TOKEN=$(cat ${TOKEN_PATH})
NAMESPACE=$(cat ${NAMESPACE_PATH})

# URL API Server (interno al cluster, service DNS)
APISERVER="https://kubernetes.default.svc"

# Deployment YAML di test
cat > /tmp/deployment-test.yaml <<'EOF'
apiVersion: apps/v1
kind: Deployment
metadata:
  name: deployment-malvagio
spec:
  replicas: 1
  selector:
    matchLabels:
      app: bad-deploy
  template:
    metadata:
      labels:
        app: bad-deploy
    spec:
      containers:
      - name: nginx
        image: nginx:1.25
        ports:
        - containerPort: 80
EOF

# Invio la richiesta all’API Server autenticandomi con il token
curl -k \
  --cacert ${CA_PATH} \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "Content-Type: application/yaml" \
  -X POST \
  --data-binary @/tmp/deployment-test.yaml \
  ${APISERVER}/apis/apps/v1/namespaces/${NAMESPACE}/deployments

echo "Pod Creato"