#!/usr/bin/env bash
set -euo pipefail

# ==========================
# Check-only bootstrap (no installs)
# ==========================

# ---- Paths ----
SCRIPTS_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMMON_LIB="${SCRIPTS_ROOT}/lib/common.sh"
if [[ ! -f "${COMMON_LIB}" ]]; then
  printf "[ERROR] Common library not found: %s\n" "${COMMON_LIB}" >&2
  return 1 2>/dev/null || exit 1
fi
source "${COMMON_LIB}"

# ---- Logging ----
info(){ log "$*"; }

# scripts sourced by your launcher
NEEDED_SOURCED_FILES=(
  "01_kind_cluster.sh"
  "02_setup_underlay.sh"
  "03_run_underlay.sh"
  "04_build_deploy.sh"
  "05_setup_k8s.sh"
)

# ---- Requirements (from all your scripts 1,2,4,5 + new launcher) ----
CORE_CMDS=( docker kubectl helm curl openssl htpasswd )
UTIL_CMDS=( grep sed awk xargs sort head tr cut base64 mktemp jq )
# need at least one of:
ALT_NET_CMDS=( ss netstat )

# ==========================
# Checks
# ==========================

# 1) Bash version
bash_major="$(bash -c 'echo ${BASH_VERSINFO[0]:-0}' 2>/dev/null || echo 0)"
if [[ "$bash_major" -lt 4 ]]; then
  fail "Your Bash is too old (<4). Please run this project with Bash 4+ (preferably 5+)."
fi
info "Bash version OK (>=4)."

# 2) Core commands present?
for c in "${CORE_CMDS[@]}"; do
  command -v "$c" >/dev/null 2>&1 || fail "Missing required command: '$c' in PATH."
done
info "Core commands present: ${CORE_CMDS[*]}"

# 3) Utilities present?
for u in "${UTIL_CMDS[@]}"; do
  command -v "$u" >/dev/null 2>&1 || fail "Missing required utility: '$u' in PATH."
done
info "Utility commands present."

# 4) Network CLI: need ss OR netstat
if command -v ss >/dev/null 2>&1; then
  info "Network check command available: ss"
elif command -v netstat >/dev/null 2>&1; then
  info "Network check command available: netstat"
else
  fail "Missing required network CLI: need either 'ss' (preferred) or 'netstat' in PATH."
fi

# 5) Cluster manager command present for selected TARGET
TARGET="${TARGET:-$([ "${KIND_CLUSTER:-0}" = "0" ] && echo "minikube" || echo "kind")}"
case "$TARGET" in
  kind)
    command -v kind >/dev/null 2>&1 || fail "Missing required command for TARGET=kind: 'kind'."
    ;;
  minikube)
    command -v minikube >/dev/null 2>&1 || fail "Missing required command for TARGET=minikube: 'minikube'."
    ;;
  *)
    fail "Invalid TARGET='${TARGET}'. Allowed values: kind|minikube."
    ;;
esac
info "Cluster manager available for TARGET=${TARGET}."

# 6) Docker: CLI reachable and daemon responsive
if ! docker --version >/dev/null 2>&1; then
  fail "Docker CLI not working."
fi
if ! docker ps >/dev/null 2>&1; then
  fail "Docker daemon is not reachable (e.g., not running or no permission to access the socket)."
fi
info "Docker CLI and daemon reachable."

# 7) Docker Buildx available (used by your builds)
if ! docker buildx version >/dev/null 2>&1; then
  fail "Docker Buildx is not available. Please enable/install Buildx before proceeding."
fi
info "Docker Buildx available."

# 8) kubectl: client reachable
if ! kubectl version --client >/dev/null 2>&1; then
  fail "kubectl client not working."
fi
info "kubectl client OK."

# 9) Helm works
if ! helm version --short >/dev/null 2>&1; then
  fail "Helm is not working."
fi
info "Helm CLI OK."

# 10) Project files expected by your launcher script
for rel in "${NEEDED_SOURCED_FILES[@]}"; do
  [[ -f "${SCRIPTS_ROOT}/${rel}" ]] || fail "Expected script not found: ${SCRIPTS_ROOT}/${rel}"
done
info "All required project files are present."

echo
info "All checks passed. Your environment satisfies the required dependencies."
