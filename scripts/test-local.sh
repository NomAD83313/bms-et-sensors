#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
VENV_PYTHON="${PROJECT_ROOT}/.venv/bin/python"

if [[ ! -x "${VENV_PYTHON}" ]]; then
  echo "ERROR: local virtualenv is missing. Run ./scripts/setup-local-python.sh first." >&2
  exit 1
fi

cd "${PROJECT_ROOT}"
if [[ "$#" -eq 0 ]]; then
  "${VENV_PYTHON}" -m unittest discover -s tests -q
else
  "${VENV_PYTHON}" -m unittest -q "$@"
fi
