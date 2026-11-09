#!/usr/bin/env bash
set -euo pipefail

# /gemini: Скрипт управляет полным циклом перезапуска стека с учетом архитектуры хоста.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/lib/compose-common.sh"

bms_cd_project

echo ">>> Rebuild and restart stack..."

ARCH="$(uname -m)"
STACK_SERVICES=(
  influxdb
  grafana
  graf-lite
  ap-control
  service-controller
  almemo-collector
  pyrometer-collector
  dashboard
  mscl-collector
  redlab-collector
)

echo ">>> Building app images for current host architecture (${ARCH})..."
bms_build_services "${APP_BUILD_SERVICES[@]}"

echo ">>> Restarting full stack services..."
bms_up_services "${STACK_SERVICES[@]}"
bms_verify_services_created "${STACK_SERVICES[@]}"

echo ">>> Stack refreshed on ${ARCH}."
echo ">>> Note: Matter profile is not managed by restart-local.sh."
echo ">>> To start/restart Matter services, run: ./scripts/restart-matter-server.sh"
