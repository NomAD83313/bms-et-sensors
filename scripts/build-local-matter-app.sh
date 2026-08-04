#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/lib/compose-common.sh"

bms_cd_project

echo ">>> Building matter-collector locally..."
bms_build_services matter-collector
echo ">>> Restarting matter-collector with matter profile without touching matter-server..."
"${COMPOSE[@]}" --profile matter up -d --no-deps matter-collector
bms_verify_services_created matter-collector
echo ">>> matter-collector ready."
