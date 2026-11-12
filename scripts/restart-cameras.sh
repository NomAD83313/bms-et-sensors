#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/lib/compose-common.sh"
bms_cd_project
if [[ -f runtime/camera-manager/enabled ]]; then
  exec "${SCRIPT_DIR}/restart-cameras-shared.sh"
fi

bms_ensure_camera_onnx_runtime
echo ">>> Building and starting camera recorder, dashboard and visual analysis..."
# camera-analysis is the shared analysis worker for all cameras.
services=(camera-recorder camera-dashboard camera-analysis)
"${COMPOSE[@]}" --profile cameras up -d --build "${services[@]}"
# Camera containers use Compose-generated names (project-service-1), so verify by service.
for service in "${services[@]}"; do
  if [[ -z "$("${COMPOSE[@]}" --profile cameras ps -a -q "$service")" ]]; then
    echo "ERROR: Compose service was not created: $service" >&2
    exit 1
  fi
done
echo ">>> Verified camera services exist: ${services[*]}"
