#!/usr/bin/env bash
# Run the camera unit tests inside the test image (runtime plus the PyTorch reference).
# Usage: scripts/test-cameras.sh [unittest arguments, e.g. tests.test_cameras_patchcore]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/lib/compose-common.sh"
bms_cd_project

# The runtime image may use the locally built XNNPACK wheel image.
bms_ensure_camera_onnx_runtime
runtime_image="${CAMERA_ONNX_RUNTIME_IMAGE:-$(read_env_var CAMERA_ONNX_RUNTIME_IMAGE)}"
docker build -q -f Dockerfile.camera-analysis --target test \
  --build-arg "ONNX_RUNTIME_IMAGE=${runtime_image:-onnx-cpu}" -t bms-camera-analysis:test . >/dev/null

if [[ $# -eq 0 ]]; then
  set -- discover -s tests -p 'test_camera*.py'
fi
docker run --rm -v "$PWD:/work" -w /work -e PYTHONDONTWRITEBYTECODE=1 \
  bms-camera-analysis:test python -m unittest "$@"
