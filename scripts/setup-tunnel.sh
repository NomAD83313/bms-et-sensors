#!/usr/bin/env bash
# Guide Cloudflare setup for a new host and start its Compose connector.
set -euo pipefail

if [[ "$#" == 1 && ( "$1" == -h || "$1" == --help ) ]]; then
  echo "Usage: ${0##*/} <public-hostname>"
  exit 0
fi

public_hostname="${1:-}"
dns_label='[a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?'
if [[ "$#" != 1 || ! "$public_hostname" =~ ^${dns_label}(\.${dns_label})+$ ]]; then
  echo "Usage: ${0##*/} <public-hostname> (e.g. ets2.douzhyk.net)" >&2
  exit 2
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/lib/compose-common.sh"
cd "${PROJECT_ROOT}"

if [[ ! -f .env ]]; then
  (umask 077; prepare_env_file)
fi

cat <<EOF
1. Open https://dash.cloudflare.com/ → Networking → Tunnels → Create a tunnel.
   Choose Cloudflared and give this host its own tunnel (e.g. ${public_hostname%%.*}).
2. Select Docker and copy the value after --token from the install command.
   Set CLOUDFLARE_TUNNEL_TOKEN in ${PROJECT_ROOT}/.env to that value.
   If the token is missing, save .env and rerun ./scripts/setup-tunnel.sh ${public_hostname}.
3. In Zero Trust → Access controls → Applications, create a self-hosted app
   for ${public_hostname} with an empty path and an Allow policy for your users.
4. In the new tunnel's Routes tab, add a Published application for
   ${public_hostname}, with an empty path and service URL http://dashboard:80.
5. Open https://${public_hostname}/ and sign in to verify the dashboard.

EOF

exec env -u CLOUDFLARE_TUNNEL_TOKEN ENV_FILE="${PROJECT_ROOT}/.env" \
  "${SCRIPT_DIR}/restart-tunnel.sh"
