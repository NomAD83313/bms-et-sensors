#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/lib/build-node.sh" \
  "esp32c6zero/multinode" "ESP32-C6-Zero Multinode" \
  "esp32c6_multinode.bin" ""
