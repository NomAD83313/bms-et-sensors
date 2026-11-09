#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/lib/compose-common.sh"

bms_cd_project

echo ">>> Building graf-lite image for current host architecture..."
bms_build_services graf-lite

echo ">>> Restarting graf-lite..."
bms_up_services_no_deps graf-lite

echo ">>> Done (graf-lite)."
