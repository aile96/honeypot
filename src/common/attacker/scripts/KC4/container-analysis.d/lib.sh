#!/usr/bin/env bash

ce_init() {
  TS="${TS:-$(date +%Y%m%d_%H%M%S)}"
  OUTDIR="${OUTDIR:-/tmp/exfiltration/container-enum-${TS}}"
  LOG="${LOG:-${OUTDIR}/report.txt}"
  JSON="${JSON:-${OUTDIR}/report.json}"
  SA_DIR="${SA_DIR:-/var/run/secrets/kubernetes.io/serviceaccount}"
  SOCKETS=(/var/run/docker.sock /run/containerd/containerd.sock /var/run/crio/crio.sock)
  BINARIES=(kubectl oc docker crictl curl wget nc ncat netcat ss jq python3 python perl busybox stat)
  mkdir -p "${OUTDIR}"
  {
    echo "Container enumeration report"
    echo "Timestamp: $(date -u +"%Y-%m-%dT%H:%M:%SZ")"
    echo "Output directory: ${OUTDIR}"
    echo "----"
  } > "${LOG}"
}

ce_install_optional_tools() {
  if command -v apt-get >/dev/null 2>&1; then
    DEBIAN_FRONTEND=noninteractive apt-get -qq update >/dev/null 2>&1 &&
      apt-get -qq install -y iproute2 psmisc jq iptables cron >/dev/null 2>&1 || true
  elif command -v apk >/dev/null 2>&1; then
    apk add --no-cache iproute2 psmisc jq iptables cronie procps >/dev/null 2>&1 || true
  fi
}

ce_section() {
  printf '\n== %s ==\n' "$1" | tee -a "${LOG}"
}

ce_run() {
  local title="$1"; shift
  ce_section "${title}"
  echo "### ${title}" >> "${OUTDIR}/raw.txt"
  if command -v "$1" >/dev/null 2>&1 || [[ -x "$1" ]]; then
    "$@" 2>&1 | tee -a "${LOG}" || true
  else
    echo "COMMAND_MISSING: $1" | tee -a "${LOG}"
  fi
}

ce_api_get() {
  local path="$1"
  local token
  command -v curl >/dev/null 2>&1 || { echo "curl not found; skipping API probes" | tee -a "${LOG}"; return 0; }
  token="$(cat "${SA_DIR}/token")"
  curl --max-time 10 -sSk -H "Authorization: Bearer ${token}" "https://kubernetes.default.svc${path}" || true
}
