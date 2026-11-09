#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/lib/compose-common.sh"

bms_cd_project

echo ">>> Building mscl-collector image for current host architecture..."
bms_build_services mscl-collector

echo ">>> Restarting mscl-collector..."
bms_up_services_no_deps mscl-collector

echo ">>> Done (mscl-collector)."
