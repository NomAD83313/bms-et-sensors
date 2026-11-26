#!/usr/bin/env bash
# Start or restart the Cloudflare Tunnel (Compose profile `tunnel`).
# Safe to rerun. See docs/remote-access.md.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/lib/compose-common.sh"

bms_cd_project

ENV_FILE="${ENV_FILE:-${PROJECT_ROOT}/.env}"
token=""
if [[ -f "${ENV_FILE}" ]]; then
  token="$(sed -n 's/^CLOUDFLARE_TUNNEL_TOKEN=//p' "${ENV_FILE}" | tail -n 1 | tr -d '"'"'"' \r')"
fi

if [[ -z "${token}" ]]; then
  echo "ERROR: CLOUDFLARE_TUNNEL_TOKEN is empty in ${ENV_FILE}." >&2
  echo "ERROR: Create a tunnel in Cloudflare Zero Trust and put its token there (docs/remote-access.md)." >&2
  exit 1
fi

echo ">>> Starting Cloudflare Tunnel (cloudflared -> dashboard:80)..."
"${COMPOSE[@]}" --env-file "${ENV_FILE}" --profile tunnel up -d --force-recreate cloudflared
bms_verify_services_created cloudflared

echo ">>> Waiting for the tunnel connection..."
for _ in $(seq 1 20); do
  if docker logs cloudflared 2>&1 | grep -F "Registered tunnel connection" >/dev/null; then
    echo ">>> Done. The tunnel is connected."
    exit 0
  fi
  sleep 1
done

echo "WARNING: no registered connection yet; check ./scripts/logs.sh cloudflared" >&2
