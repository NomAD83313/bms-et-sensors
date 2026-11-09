#!/usr/bin/env bash
set -euo pipefail

# /gemini: Оркестратор первичной настройки DAQ-стека с проверкой зависимостей и Docker-окружения.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${PROJECT_ROOT}"

echo "==========================================="
echo " Raspberry Pi DAQ Stack Setup Utility"
echo "==========================================="

PROJECT_DIR="${PROJECT_DIR:-$HOME/bms-et-sensors}"
REPO_URL="${REPO_URL:-https://github.com/NomAD83313/bms-et-sensors.git}"
REPO_BRANCH="${REPO_BRANCH:-main}"
RETRY_ATTEMPTS="${RETRY_ATTEMPTS:-5}"
RETRY_DELAY_SEC="${RETRY_DELAY_SEC:-8}"
RPI_SETUP_ENABLE_PYROMETERS="${RPI_SETUP_ENABLE_PYROMETERS:-1}"
RPI_SETUP_RUN_PYROMETERS_SETUP="${RPI_SETUP_RUN_PYROMETERS_SETUP:-${RPI_SETUP_ENABLE_PYROMETERS}}"
DOCKER_CMD=(docker)
COMPOSE_CMD=(docker compose)
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

source "${SCRIPT_DIR}/lib/setup-common.sh"

pull_base_images() {
  local images=(
    "python:3.12-slim"
    "influxdb:2.7"
    "grafana/grafana-oss"
    "nginx:latest"
  )

  echo ">>> Pre-pulling base images (with retries)..."
  local image=""
  for image in "${images[@]}"; do
    retry_cmd "docker pull ${image}" "${DOCKER_CMD[@]}" pull "${image}"
  done
}

maybe_disable_pyrometers() {
  if [ "${RPI_SETUP_ENABLE_PYROMETERS}" = "1" ]; then
    return
  fi
  echo ">>> thermoMETER stack disabled by RPI_SETUP_ENABLE_PYROMETERS=0"
  remove_stack_service "pyrometer-collector"
}

build_and_start_stack() {
  echo ">>> Building local ARM-compatible app images..."
  retry_cmd "docker compose build app services" \
    "${COMPOSE_CMD[@]}" -f docker-compose.yml -f docker-compose.override.yml build --no-cache mscl-collector redlab-collector graf-lite ap-control service-controller almemo-collector pyrometer-collector

  maybe_disable_pyrometers

  echo ">>> Starting full stack..."
  retry_cmd "docker compose up" \
    "${COMPOSE_CMD[@]}" -f docker-compose.yml -f docker-compose.override.yml up -d "${STACK_SERVICES[@]}"
  verify_stack_services_created "${STACK_SERVICES[@]}"
}

show_post_checks() {
  echo ">>> Stack status:"
  "${COMPOSE_CMD[@]}" ps
  echo ">>> Tail logs examples:"
  echo "    ./scripts/logs.sh mscl-collector"
  echo "    ./scripts/logs.sh redlab-collector"
  echo "    ./scripts/logs.sh almemo-collector"
  echo "    ./scripts/logs.sh pyrometer-collector"
  echo "    ./scripts/logs.sh ap-control"
}

install_prerequisites
install_docker_if_needed
resolve_docker_access
ensure_compose_plugin
disable_modemmanager_if_present
disable_avahi_if_present
prepare_repo
prepare_env_file
if [ "${RPI_SETUP_RUN_PYROMETERS_SETUP}" = "1" ]; then
  run_pyrometers_setup
fi
pull_base_images
build_and_start_stack
show_post_checks

echo "==========================================="
echo " Setup finished."
echo "==========================================="
