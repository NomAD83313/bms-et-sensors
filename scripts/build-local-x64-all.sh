#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

HOST_ARCH="$(uname -m)"
case "${HOST_ARCH}" in
  x86_64|amd64)
    ;;
  *)
    echo "build-local-x64-all.sh must be run on a native x64 host."
    echo "Current architecture: ${HOST_ARCH}"
    exit 1
    ;;
esac

echo ">>> Native x64 local build selected (no cross-compilation)."
exec "${SCRIPT_DIR}/build-local-all.sh"
