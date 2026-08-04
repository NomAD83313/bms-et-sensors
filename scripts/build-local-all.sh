#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/lib/compose-common.sh"

bms_cd_project

echo ">>> Building local app images for current host architecture..."
bms_build_services "${APP_BUILD_SERVICES[@]}"

echo ">>> Restarting default local app services..."
bms_up_services_no_deps "${DEFAULT_APP_BUILD_SERVICES[@]}"

echo ">>> Restarting optional Matter app service without touching matter-server..."
"${COMPOSE[@]}" --profile matter up -d --no-deps matter-collector
bms_verify_services_created matter-collector

echo ">>> Done (${APP_BUILD_SERVICES[*]})."
