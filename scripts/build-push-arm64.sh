#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${PROJECT_ROOT}"

DOCKER_NAMESPACE="${DOCKER_NAMESPACE:-nomad375}"
PLATFORM="linux/arm64"
HOST_ARCH="$(uname -m)"

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

  echo ">>> Building and pushing ${image_name} (${PLATFORM})..."
  docker build \
    --platform "${PLATFORM}" \
    -f "${dockerfile}" \
    -t "${DOCKER_NAMESPACE}/${image_name}:latest" \
    --pull \
    --no-cache \
    .
  docker push "${DOCKER_NAMESPACE}/${image_name}:latest"
}

build_and_push Dockerfile.mscl bms-mscl-collector
build_and_push Dockerfile.redlab bms-redlab-collector
build_and_push Dockerfile.graf-app bms-graf-lite
build_and_push Dockerfile.ap-ui bms-ap-control
build_and_push Dockerfile.svcctl bms-service-controller
build_and_push Dockerfile.almemo bms-almemo-collector
build_and_push Dockerfile.pyrometers bms-pyrometer-collector
build_and_push Dockerfile.matter-app bms-matter-collector

echo ">>> arm64 push complete (bms-mscl-collector/bms-redlab-collector/bms-graf-lite/bms-ap-control/bms-service-controller/bms-almemo-collector/bms-pyrometer-collector/bms-matter-collector)."
