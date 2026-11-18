#!/usr/bin/env bash
set -euo pipefail

# Numbered compatibility entry point for updating an existing installation.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "${SCRIPT_DIR}/update-from-github.sh" "$@"
