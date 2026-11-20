#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/lib/build-node.sh" \
  "esp32c3SuperMini/matter-node" "ESP32-C3-SuperMini" \
  "esp32c3_matter_node.bin" ""
