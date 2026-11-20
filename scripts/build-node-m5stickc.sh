#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/lib/build-node.sh" \
  "m5stickc/matter-node" "M5StickC Matter" \
  "m5stickc_matter_node.bin" "m5stickc_matter_node-ota.bin"
