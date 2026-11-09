#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/lib/compose-common.sh"

bms_cd_project

echo ">>> Building service-controller locally..."
bms_build_services service-controller
echo ">>> Restarting service-controller..."
bms_up_services service-controller
echo ">>> service-controller ready."
