#!/usr/bin/env bash
set -euo pipefail

# Usage:
# ./scripts/logs.sh             -> shows logs for all containers
# ./scripts/logs.sh influxdb    -> shows logs for InfluxDB only

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/lib/compose-common.sh"

bms_cd_project

if [[ "${1:-}" == "" ]]; then
    echo ">>> Following logs for ALL containers (Ctrl+C to exit)..."
    bms_logs
else
    echo ">>> Following logs for: $1 (Ctrl+C to exit)..."
    bms_logs "$1"
fi
