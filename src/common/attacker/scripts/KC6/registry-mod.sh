#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/registry_mod_common.sh
source "${SCRIPT_DIR}/lib/registry_mod_common.sh"

registry_prepare_build_args
registry_build_and_push
