#!/usr/bin/env bash
# Apply runtime/cameras.json: generate the extra cameras' Compose services, analysis list and
# nginx routes, then start them. Every camera is analysed by the shared worker, which is restarted
# so that it picks up the new camera list.
# Usage: scripts/cameras-apply.sh        (edit runtime/cameras.json first; see docs/camera-instances.md)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/lib/compose-common.sh"
bms_cd_project
if [[ -f runtime/camera-manager/enabled ]]; then
  echo 'Shared camera deployment: add cameras in http://<host>/cameras/.'
  exit 0
fi

if [[ ! -f runtime/cameras.json ]]; then
  cp config/cameras.example.json runtime/cameras.json
  echo ">>> Created runtime/cameras.json from config/cameras.example.json"
fi

python3 app/cameras/camera_registry.py generate
# The generated Compose file may have appeared or disappeared: rebuild the command line.
COMPOSE=(docker compose -f docker-compose.yml -f docker-compose.override.yml)
if [[ -f runtime/docker-compose.cameras.yml ]]; then
  COMPOSE+=(-f runtime/docker-compose.cameras.yml)
fi
PROFILES=(--profile cameras-extra)

mapfile -t extra < <(python3 app/cameras/camera_registry.py list | cut -f1)
services=()
for id in "${extra[@]}"; do
  [[ -n "$id" ]] && services+=("cam-${id}-recorder" "cam-${id}-dashboard")
done

# Remove the containers of cameras that are no longer in the registry.
keep=" ${extra[*]:-} "
while read -r container id; do
  [[ -z "${container:-}" ]] && continue
  if [[ "$keep" != *" ${id} "* ]]; then
    echo ">>> Removing camera ${id} (no longer in runtime/cameras.json)"
    docker rm -f "$container" >/dev/null
  fi
done < <(docker ps -a --filter label=bms.camera.id --format '{{.ID}} {{.Label "bms.camera.id"}}')

if (( ${#services[@]} )); then
  bms_ensure_camera_onnx_runtime
  echo ">>> Starting extra cameras: ${extra[*]}"
  "${COMPOSE[@]}" "${PROFILES[@]}" up -d --build "${services[@]}"
fi
# The shared worker reads the camera list at start and needs each camera's directories mounted.
echo ">>> Restarting the shared analysis worker"
"${COMPOSE[@]}" "${PROFILES[@]}" up -d --build --force-recreate camera-analysis

echo ">>> Reloading nginx routes"
"${COMPOSE[@]}" exec -T dashboard nginx -t
"${COMPOSE[@]}" exec -T dashboard nginx -s reload
for id in "${extra[@]}"; do
  [[ -n "$id" ]] && echo "    /cameras/${id}/"
done
