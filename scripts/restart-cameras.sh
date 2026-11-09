#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/lib/compose-common.sh"
bms_cd_project

echo ">>> Building and starting camera recorder, dashboard and visual analysis..."
"${COMPOSE[@]}" --profile cameras up -d --build camera-recorder camera-dashboard camera-anomaly
bms_verify_services_created camera-recorder camera-dashboard camera-anomaly
