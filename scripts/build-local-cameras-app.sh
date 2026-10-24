#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/lib/compose-common.sh"

bms_cd_project
bms_build_services cameras-app
bms_up_services_no_deps cameras-app
bms_verify_services_created cameras-app
