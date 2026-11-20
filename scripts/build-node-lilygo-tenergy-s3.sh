#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

idf_args=()
if [[ -n "${BMS_RGB_LED_GPIO:-}" ]]; then
  idf_args+=("-D" "BMS_RGB_LED_GPIO=${BMS_RGB_LED_GPIO}")
fi

source "${SCRIPT_DIR}/lib/build-node.sh" \
  "lilygoTEnergyS3/matter-wifi-node" "LILYGO T-Energy-S3 Matter Wi-Fi" \
  "lilygo_tenergy_s3_matter_wifi_node.bin" "lilygo_tenergy_s3_matter_wifi_node-ota.bin" "${idf_args[@]}"
