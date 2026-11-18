#!/usr/bin/env bash
# Enable the optional MediaMTX relay (camera-hub): one upstream RTSP connection per camera.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/lib/compose-common.sh"
bms_cd_project
if [[ ! -f runtime/camera-manager/cameras.json ]]; then
  echo 'Start the cameras first: ./scripts/restart-cameras.sh' >&2
  exit 1
fi
# Bootstrap a private config before starting the relay. Subsequent camera additions
# are synchronized by camera-media; the mounted directory permits atomic reloads.
mkdir -p runtime/camera-hub
sudo python3 - <<'PY'
import json
from pathlib import Path
from app.cameras.stream_hub import StreamHub
rows = json.loads(Path('runtime/camera-manager/cameras.json').read_text())['cameras']
for camera in rows:
    base = Path('runtime/pi-camera') if camera['id'] == 'pi' else Path('runtime/cameras-extra') / camera['id']
    path = base / 'anomaly/status.json'
    if path.exists() and json.loads(path.read_text()).get('state') in ('learning', 'training'):
        raise SystemExit('Finish camera learning before restarting camera media')
StreamHub('runtime/camera-hub/mediamtx.yml', 'config/camera-hub.example.json',
          'rtsp://127.0.0.1:8554').sync(rows)
PY
COMPOSE=(docker compose -f docker-compose.yml -f docker-compose.override.yml -f docker-compose.camera-hub.yml)
"${COMPOSE[@]}" --profile cameras pull camera-hub
"${COMPOSE[@]}" --profile cameras build camera-media service-controller
"${COMPOSE[@]}" --profile cameras up -d --no-deps --no-build camera-hub
touch runtime/camera-hub/enabled
"${COMPOSE[@]}" --profile cameras up -d --no-deps --no-build camera-media service-controller
echo 'Shared MediaMTX relay enabled. RTSP cameras use one upstream connection each.'
