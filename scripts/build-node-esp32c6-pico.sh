#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/lib/build-node.sh" \
  "esp32c6Pico/matter-thread-node" "ESP32-C6-Pico" \
  "esp32c6_pico_matter_node.bin" ""
