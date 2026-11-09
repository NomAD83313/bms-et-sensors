#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo ">>> build-push-multiarch.sh is deprecated."
echo ">>> Redirecting to arm64-only Docker Hub publish flow..."

exec "${SCRIPT_DIR}/build-push-arm64.sh"
