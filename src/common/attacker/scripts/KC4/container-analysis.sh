#!/usr/bin/env bash
# Purpose: complete container enumeration (INFO-ONLY), split into small phases.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PHASE_DIR="${SCRIPT_DIR}/container-analysis.d"
# shellcheck source=container-analysis.d/lib.sh
source "${PHASE_DIR}/lib.sh"

ce_init
ce_install_optional_tools
for phase in 10-system 20-network 30-kubernetes 40-runtime 50-files 90-summary; do
  # shellcheck source=/dev/null
  source "${PHASE_DIR}/${phase}.sh"
done
