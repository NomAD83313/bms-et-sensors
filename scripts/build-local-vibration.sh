#!/usr/bin/env bash
set -euo pipefail

# Builds and (re)starts the WTVB01-485 vibration collector (Compose profile "vibration").
# Safe to rerun; the sensor registry in runtime/vibration/ is preserved.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/lib/compose-common.sh"

bms_cd_project

mkdir -p runtime/vibration

echo ">>> Building vibration-collector image for current host architecture..."
bms_build_services vibration-collector

echo ">>> Restarting vibration-collector with vibration profile..."
docker compose --profile vibration -f docker-compose.yml -f docker-compose.override.yml up -d --no-deps vibration-collector

echo ">>> Restarting dashboard so /vibration/ proxy route is reloaded..."
bms_restart_service dashboard

echo ">>> Done (vibration-collector). Open http://<host>/vibration/ to select the RS485 port and sensors."
