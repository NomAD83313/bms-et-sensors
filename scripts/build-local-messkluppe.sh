#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/lib/compose-common.sh"

bms_cd_project

echo ">>> Building messkluppe-collector image for current host architecture..."
bms_build_services messkluppe-collector

echo ">>> Restarting messkluppe-collector with messkluppe profile..."
docker compose --profile messkluppe -f docker-compose.yml -f docker-compose.override.yml up -d --no-deps messkluppe-collector

echo ">>> Restarting dashboard so /messkluppe/ proxy route is reloaded..."
bms_restart_service dashboard

echo ">>> Done (messkluppe-collector)."
