#!/usr/bin/env bash
# Prepare optional camera images during Raspberry Pi setup without starting cameras.
# Builds the XNNPACK ONNX Runtime wheel image once (when CAMERA_ONNX_RUNTIME_IMAGE
# selects it) and the camera application images, so the first camera start is fast.
# Safe to rerun: existing images are reused. Use --rebuild-onnx to recompile ONNX Runtime.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/lib/compose-common.sh"
bms_cd_project

rebuild_onnx=0
for argument in "$@"; do
  case "$argument" in
    --rebuild-onnx) rebuild_onnx=1 ;;
    -h|--help)
      echo "Usage: $0 [--rebuild-onnx]"
      exit 0
      ;;
    *)
      echo "Unknown option: $argument" >&2
      exit 1
      ;;
  esac
done

echo "==========================================="
echo " Camera setup: ONNX Runtime and camera images"
echo "==========================================="

prepare_env_file

if [[ "$rebuild_onnx" == "1" ]]; then
  image="${CAMERA_ONNX_RUNTIME_IMAGE:-$(read_env_var CAMERA_ONNX_RUNTIME_IMAGE)}"
  if [[ "$image" == "bms-onnx-xnnpack:local" ]] && docker image inspect "$image" >/dev/null 2>&1; then
    echo ">>> Removing $image to force a rebuild..."
    docker image rm "$image" >/dev/null
  fi
fi

bms_ensure_camera_onnx_runtime

echo ">>> Building camera images (no containers are started)..."
"${COMPOSE[@]}" --profile cameras --profile pi-camera build \
  camera-recorder camera-dashboard camera-anomaly \
  pi-camera-recorder pi-camera-dashboard pi-camera-anomaly

echo "==========================================="
echo " Camera setup finished. Start a camera with:"
echo "   ./scripts/restart-cameras.sh      # RTSP camera (set CAMERA_URL in .env)"
echo "   ./scripts/restart-pi-camera.sh    # Raspberry Pi CSI camera"
echo "==========================================="
