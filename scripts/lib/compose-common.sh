#!/usr/bin/env bash

: "${SCRIPT_DIR:?SCRIPT_DIR must be set before sourcing compose-common.sh}"

PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
COMPOSE=(docker compose -f docker-compose.yml -f docker-compose.override.yml)
DEFAULT_APP_BUILD_SERVICES=(mscl-collector redlab-collector graf-lite service-controller almemo-collector pyrometer-collector ap-control)
OPTIONAL_APP_BUILD_SERVICES=(matter-collector)
APP_BUILD_SERVICES=("${DEFAULT_APP_BUILD_SERVICES[@]}" "${OPTIONAL_APP_BUILD_SERVICES[@]}")

# shellcheck disable=SC1091
source "${SCRIPT_DIR}/lib/setup-common.sh"

bms_cd_project() {
  cd "${PROJECT_ROOT}"
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
