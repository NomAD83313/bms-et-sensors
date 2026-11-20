#!/usr/bin/env bash

: "${SCRIPT_DIR:?SCRIPT_DIR must be set before sourcing compose-common.sh}"

PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
COMPOSE=(docker compose -f docker-compose.yml -f docker-compose.override.yml)
# Optional RTSP relay for the cameras, enabled by scripts/restart-camera-hub.sh.
if [[ -f "${SCRIPT_DIR}/../runtime/camera-hub/enabled" ]]; then
  COMPOSE+=(-f docker-compose.camera-hub.yml)
fi
DEFAULT_APP_BUILD_SERVICES=(mscl-collector redlab-collector graf-lite service-controller almemo-collector pyrometer-collector ap-control)

# shellcheck disable=SC1091
source "${SCRIPT_DIR}/lib/setup-common.sh"

bms_cd_project() {
  cd "${PROJECT_ROOT}"
  # Bind-mounted by Compose: create them as the current user, not as root.
  mkdir -p runtime/camera-manager runtime/cameras-extra runtime/pi-camera
}

# Docker Hub images of services whose Compose image name is local-only.
bms_hub_image_for_local() {
  case "$1" in
    camera-media) echo "nomad375/bms-camera-media:latest bms-cameras:local" ;;
    camera-analysis) echo "nomad375/bms-camera-analysis:latest bms-camera-analysis:local" ;;
  esac
}

# Build (or, with BMS_IMAGE_SOURCE=pull, download) app images: [--profile NAME]... SERVICE...
# A service whose prebuilt image cannot be pulled is built locally instead.
bms_build_services() {
  local profile_args=()
  while [[ "${1:-}" == "--profile" ]]; do
    profile_args+=("$1" "$2")
    shift 2
  done
  local image_source
  image_source="$(bms_image_source)" || return 1
  local to_build=("$@")
  if [[ "$image_source" == "pull" ]]; then
    to_build=()
    local service hub_image local_image
    for service in "$@"; do
      read -r hub_image local_image <<<"$(bms_hub_image_for_local "$service")"
      echo ">>> Pulling prebuilt image for ${service} (BMS_IMAGE_SOURCE=pull)"
      if [[ -n "$hub_image" ]]; then
        docker pull "$hub_image" && docker tag "$hub_image" "$local_image" && continue
      else
        "${COMPOSE[@]}" "${profile_args[@]}" pull "$service" && continue
      fi
      echo "WARN: no prebuilt image for ${service}; building it locally." >&2
      to_build+=("$service")
    done
    [[ "${#to_build[@]}" -eq 0 ]] && return 0
  fi
  local args=()
  if [[ "${NO_CACHE:-0}" == "1" ]]; then
    args+=(--no-cache)
  fi
  "${COMPOSE[@]}" "${profile_args[@]}" build "${args[@]}" "${to_build[@]}"
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
  if [[ "$(bms_image_source)" == "pull" ]] \
    && docker pull nomad375/bms-camera-analysis:latest >/dev/null 2>&1; then
    # The prebuilt camera-analysis image already contains ONNX Runtime with XNNPACK.
    return 0
  fi
  if docker image inspect "$image" >/dev/null 2>&1; then
    echo ">>> ONNX Runtime image present: $image"
    return 0
  fi
  if [[ "$image" != "bms-onnx-xnnpack:local" ]]; then
    echo ">>> Pulling ONNX Runtime image: $image"
    if docker pull "$image"; then
      return 0
    fi
    echo "ERROR: CAMERA_ONNX_RUNTIME_IMAGE=$image could not be pulled." >&2
    return 1
  fi
  # A fresh arm64 host first tries the published wheel image instead of a compile
  # that takes hours. CAMERA_ONNX_PREBUILT_IMAGE= (empty) always compiles locally.
  local prebuilt="${CAMERA_ONNX_PREBUILT_IMAGE-nomad375/bms-onnx-xnnpack:ort-1.31.1-arm64}"
  if [[ -n "$prebuilt" && "$(uname -m)" =~ ^(aarch64|arm64)$ ]]; then
    echo ">>> Pulling prebuilt ONNX Runtime image: $prebuilt"
    if docker pull "$prebuilt" && docker tag "$prebuilt" "$image"; then
      return 0
    fi
    echo "WARN: $prebuilt is not available; compiling ONNX Runtime locally instead." >&2
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
