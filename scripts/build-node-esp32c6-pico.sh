#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
NODE_DIR="${PROJECT_ROOT}/nodes/esp32c6Pico/matter-thread-node"

BUILD_TIMEOUT_SEC="${BUILD_TIMEOUT_SEC:-3600}"
FIRMWARE_FULLCLEAN="${FIRMWARE_FULLCLEAN:-0}"
ESP_IDF_EXPORT="${ESP_IDF_EXPORT:-${HOME}/.espressif/v5.4.1/esp-idf/export.sh}"
ESP_MATTER_PATH="${ESP_MATTER_PATH:-${HOME}/.espressif/esp-matter-release-v1.5}"
PW_PROJECT_ROOT="${PW_PROJECT_ROOT:-${ESP_MATTER_PATH}/connectedhomeip/connectedhomeip}"
PW_ROOT="${PW_ROOT:-${PW_PROJECT_ROOT}/third_party/pigweed/repo}"
PW_ENVIRONMENT_ROOT="${PW_ENVIRONMENT_ROOT:-${PW_PROJECT_ROOT}/.environment}"
PW_ENVIRONMENT_CONFIG_FILE="${PW_ENVIRONMENT_CONFIG_FILE:-${PW_PROJECT_ROOT}/scripts/setup/environment.json}"
PIGWEED_CIPD_DIR="${PIGWEED_CIPD_DIR:-${PW_ENVIRONMENT_ROOT}/cipd/packages/pigweed}"

if [[ ! -f "${ESP_IDF_EXPORT}" ]]; then
  echo "ERROR: ESP-IDF export script not found: ${ESP_IDF_EXPORT}" >&2
  exit 1
fi
if [[ ! -d "${ESP_MATTER_PATH}" ]]; then
  echo "ERROR: ESP_MATTER_PATH not found: ${ESP_MATTER_PATH}" >&2
  exit 1
fi
if [[ ! -d "${PW_PROJECT_ROOT}" ]]; then
  echo "ERROR: PW_PROJECT_ROOT not found: ${PW_PROJECT_ROOT}" >&2
  exit 1
fi
if [[ ! -d "${PW_ROOT}" ]]; then
  echo "ERROR: PW_ROOT not found: ${PW_ROOT}" >&2
  exit 1
fi
if [[ ! -d "${PW_ENVIRONMENT_ROOT}" ]]; then
  echo "ERROR: Pigweed environment not found: ${PW_ENVIRONMENT_ROOT}" >&2
  exit 1
fi
if [[ ! -f "${PW_ENVIRONMENT_CONFIG_FILE}" ]]; then
  echo "ERROR: Pigweed environment config not found: ${PW_ENVIRONMENT_CONFIG_FILE}" >&2
  exit 1
fi
if [[ ! -x "${PIGWEED_CIPD_DIR}/gn" ]]; then
  echo "ERROR: gn not found in Pigweed CIPD path: ${PIGWEED_CIPD_DIR}" >&2
  exit 1
fi
if ! command -v timeout >/dev/null 2>&1; then
  echo "ERROR: timeout command is required for build time control." >&2
  exit 1
fi
if [[ ! -d "${NODE_DIR}" ]]; then
  echo "ERROR: Node firmware directory not found: ${NODE_DIR}" >&2
  exit 1
fi

start_epoch="$(date +%s)"
echo ">>> Building ESP32-C6-Pico firmware (timeout=${BUILD_TIMEOUT_SEC}s)..."

set +e
(
  set -euo pipefail
  set +u
  # shellcheck disable=SC1090
  source "${ESP_IDF_EXPORT}"
  set -u
  export ESP_MATTER_PATH
  export PW_PROJECT_ROOT
  export PW_ROOT
  export _PW_ACTUAL_ENVIRONMENT_ROOT="${PW_ENVIRONMENT_ROOT}"
  export _PW_ENVIRONMENT_CONFIG_FILE="${PW_ENVIRONMENT_CONFIG_FILE}"
  export PATH="${PIGWEED_CIPD_DIR}:${PIGWEED_CIPD_DIR}/bin:${PW_PROJECT_ROOT}/out/host:${PATH}"
  cd "${NODE_DIR}"
  if [[ "${FIRMWARE_FULLCLEAN}" == "1" ]]; then
    idf.py fullclean
  fi
  timeout "${BUILD_TIMEOUT_SEC}" idf.py build
)
status=$?
set -e

end_epoch="$(date +%s)"
elapsed_sec=$((end_epoch - start_epoch))

if [[ ${status} -eq 124 ]]; then
  echo "ERROR: Firmware build timed out after ${elapsed_sec}s." >&2
  exit "${status}"
fi
if [[ ${status} -ne 0 ]]; then
  echo "ERROR: Firmware build failed after ${elapsed_sec}s (exit=${status})." >&2
  exit "${status}"
fi

printf '>>> Firmware build ready in %02d:%02d:%02d\n' \
  "$((elapsed_sec / 3600))" \
  "$(((elapsed_sec % 3600) / 60))" \
  "$((elapsed_sec % 60))"
echo ">>> App image: ${NODE_DIR}/build/esp32c6_pico_matter_node.bin"
