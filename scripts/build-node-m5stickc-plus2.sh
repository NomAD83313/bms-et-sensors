#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/lib/build-node.sh" \
  "m5stickcPlus2/matter-node" "M5StickC Plus2 Matter" \
  "m5stickc_plus2_matter_node.bin" "m5stickc_plus2_matter_node-ota.bin"
