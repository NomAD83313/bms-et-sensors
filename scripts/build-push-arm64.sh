#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${PROJECT_ROOT}"

DOCKER_NAMESPACE="${DOCKER_NAMESPACE:-nomad375}"
PLATFORM="linux/arm64"
HOST_ARCH="$(uname -m)"
PUSH_IMAGES="${PUSH_IMAGES:-1}"
ONNX_RUNTIME_VERSION="${ONNX_RUNTIME_VERSION:-1.31.1}"
ONNX_RUNTIME_TAG="ort-${ONNX_RUNTIME_VERSION}-arm64"

case "${HOST_ARCH}" in
  aarch64|arm64)
    ;;
  *)
    echo "build-push-arm64.sh must be run on a native arm64 host."
    echo "Current architecture: ${HOST_ARCH}"
    exit 1
    ;;
esac

build_and_push() {
  local dockerfile="$1"
  local image_name="$2"
  local image_tag="${3:-latest}"
  shift 3 || true
  local image="${DOCKER_NAMESPACE}/${image_name}:${image_tag}"
  local -a build_command=(
    docker build
    --platform "${PLATFORM}"
    -f "${dockerfile}"
    -t "${image}"
    --no-cache
  )

  # camera-analysis consumes the XNNPACK image built immediately before it. Pulling
  # that dependency would break build-only preparation before its first publication.
  if [[ "${image_name}" != "bms-camera-analysis" ]]; then
    build_command+=(--pull)
  fi
  build_command+=("$@" .)

  echo ">>> Building ${image} (${PLATFORM})..."
  "${build_command[@]}"
  if [[ "${PUSH_IMAGES}" == "1" ]]; then
    docker push "${image}"
  else
    echo ">>> Build-only mode: not pushing ${image}."
  fi
}

build_and_push Dockerfile.mscl bms-mscl-collector latest
build_and_push Dockerfile.redlab bms-redlab-collector latest
build_and_push Dockerfile.graf-app bms-graf-lite latest
build_and_push Dockerfile.ap-ui bms-ap-control latest
build_and_push Dockerfile.svcctl bms-service-controller latest
build_and_push Dockerfile.almemo bms-almemo-collector latest
build_and_push Dockerfile.pyrometers bms-pyrometer-collector latest
build_and_push Dockerfile.matter-app bms-matter-collector latest
build_and_push Dockerfile.messkluppe bms-messkluppe-collector latest
build_and_push Dockerfile.vibration bms-vibration-collector latest

# Build the reusable ONNX Runtime wheel layer first. camera-analysis consumes this
# exact versioned tag, so future Pi hosts can pull it without compiling ONNX Runtime.
build_and_push Dockerfile.camera-onnx-xnnpack bms-onnx-xnnpack "${ONNX_RUNTIME_TAG}"
docker tag "${DOCKER_NAMESPACE}/bms-onnx-xnnpack:${ONNX_RUNTIME_TAG}" bms-onnx-xnnpack:local
build_and_push Dockerfile.cameras bms-camera-media latest
build_and_push Dockerfile.camera-analysis bms-camera-analysis latest \
  --build-arg ONNX_RUNTIME_IMAGE=bms-onnx-xnnpack:local

if [[ "${PUSH_IMAGES}" == "1" ]]; then
  echo ">>> arm64 build and push complete."
else
  echo ">>> arm64 build-only preparation complete; no images were pushed."
fi
