#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/lib/compose-common.sh"

bms_cd_project

echo ">>> Building ap-control locally..."
bms_build_services ap-control
echo ">>> Restarting ap-control..."
bms_up_services_no_deps ap-control
echo ">>> Restarting dashboard to reload nginx route /ap/ ..."
bms_restart_service dashboard
echo ">>> Done (ap-control + dashboard)."
