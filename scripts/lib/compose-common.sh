#!/usr/bin/env bash

: "${SCRIPT_DIR:?SCRIPT_DIR must be set before sourcing compose-common.sh}"

PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
COMPOSE=(docker compose -f docker-compose.yml -f docker-compose.override.yml)
if [[ -f "${SCRIPT_DIR}/../runtime/camera-manager/enabled" ]]; then
  COMPOSE+=(-f docker-compose.camera-shared.yml)
fi
if [[ -f "${SCRIPT_DIR}/../runtime/camera-hub/enabled" ]]; then
  COMPOSE+=(-f docker-compose.camera-hub.yml)
fi
# Extra cameras: recorder and dashboard services generated from runtime/cameras.json.
if [[ -f "${SCRIPT_DIR}/../runtime/docker-compose.cameras.yml" ]]; then
  COMPOSE+=(-f runtime/docker-compose.cameras.yml)
fi
DEFAULT_APP_BUILD_SERVICES=(mscl-collector redlab-collector graf-lite service-controller almemo-collector pyrometer-collector ap-control)
OPTIONAL_APP_BUILD_SERVICES=(matter-collector)
APP_BUILD_SERVICES=("${DEFAULT_APP_BUILD_SERVICES[@]}" "${OPTIONAL_APP_BUILD_SERVICES[@]}")

# shellcheck disable=SC1091
source "${SCRIPT_DIR}/lib/setup-common.sh"

bms_cd_project() {
  cd "${PROJECT_ROOT}"
  # Bind-mounted by Compose: create them as the current user, not as root.
  mkdir -p runtime/camera-registry/nginx
}

bms_build_services() {
  local args=()
  if [[ "${NO_CACHE:-0}" == "1" ]]; then
    args+=(--no-cache)
  fi
  "${COMPOSE[@]}" build "${args[@]}" "$@"
}

bms_up_services() {
  "${COMPOSE[@]}" up -d "$@"
}

bms_up_services_no_deps() {
  "${COMPOSE[@]}" up -d --no-deps "$@"
}

bms_verify_services_created() {
  verify_stack_services_created "$@"
}

# Build the optional XNNPACK ONNX Runtime wheel image once, before camera analysis
# images are built from it. A shell value overrides .env, as in Docker Compose.
# Remove the image (docker image rm bms-onnx-xnnpack:local) to force a rebuild.
bms_ensure_camera_onnx_runtime() {
  local image="${CAMERA_ONNX_RUNTIME_IMAGE:-$(read_env_var CAMERA_ONNX_RUNTIME_IMAGE)}"
  if [[ -z "$image" || "$image" == "onnx-cpu" ]]; then
    return 0
  fi
  if docker image inspect "$image" >/dev/null 2>&1; then
    echo ">>> ONNX Runtime image present: $image"
    return 0
  fi
  if [[ "$image" != "bms-onnx-xnnpack:local" ]]; then
    echo "ERROR: CAMERA_ONNX_RUNTIME_IMAGE=$image is missing and is not built by this project." >&2
    echo "       Use onnx-cpu or bms-onnx-xnnpack:local." >&2
    return 1
  fi
  echo ">>> Building $image once: compiling ONNX Runtime with XNNPACK."
  echo ">>> This is a one-time step and can take a long time on a Raspberry Pi."
  docker build -f Dockerfile.camera-onnx-xnnpack -t "$image" .
}

bms_restart_service() {
  "${COMPOSE[@]}" restart "$1"
}

bms_logs() {
  if [[ "${1:-}" == "" ]]; then
    "${COMPOSE[@]}" logs -f
    return
  fi
  "${COMPOSE[@]}" logs -f "$1"
}
