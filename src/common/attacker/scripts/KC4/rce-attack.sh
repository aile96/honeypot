#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/rce_common.sh
source "${SCRIPT_DIR}/lib/rce_common.sh"

kc_rce_load_config
kc_rce_require_helpers
kc_rce_discover_nodes
kc_rce_find_target
kc_rce_ensure_key
kc_rce_trigger
