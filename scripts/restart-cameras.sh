#!/usr/bin/env bash
# Build and (re)start the camera stack: camera-media (all cameras, UI at /cameras/),
# camera-analysis (shared visual analysis) and, when enabled, the camera-hub RTSP relay.
# Cameras are added, edited and started in the UI; their registry is the private runtime file
# runtime/camera-manager/cameras.json. The Pi CSI camera's host source is installed or
# updated first when the registry contains it. Safe to rerun.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/lib/compose-common.sh"
bms_cd_project

pi_tools=0
if "${SCRIPT_DIR}/pi-camera-host-setup.sh" --check; then
  pi_tools=1
fi

# Camera state and the private registry may be owned by container root.
pi_registered="$(sudo env PI_TOOLS="$pi_tools" python3 - <<'PY'
import json, os
from pathlib import Path
from app.cameras.camera_manager import validate_camera, validate_saved_preprocessing

def base(identifier):
    return Path('runtime/pi-camera') if identifier == 'pi' else Path('runtime/cameras-extra') / identifier

for status in [Path('runtime/pi-camera/anomaly/status.json'), *Path('runtime/cameras-extra').glob('*/anomaly/status.json')]:
    if status.exists() and json.loads(status.read_text()).get('state') in ('learning', 'training'):
        raise SystemExit(f'Camera {status.parent.parent.name} is learning; pause it before restarting the camera services')

registry = Path('runtime/camera-manager/cameras.json')
if not registry.exists():
    data = json.loads(Path('config/cameras.example.json').read_text())
    if os.environ['PI_TOOLS'] == '1':
        # The local Pi camera is the only camera this script can register by itself.
        data['cameras'].append(dict(id='pi', label='Raspberry Pi Camera', url='http://127.0.0.1:8084/stream.ts',
                                    capture_url='http://127.0.0.1:8084', enabled=True))
    descriptor = os.open(registry, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'w') as stream:
        json.dump(data, stream)

rows = [validate_camera(row) for row in json.loads(registry.read_text())['cameras']]
if len(rows) > 16 or len({c['id'] for c in rows}) != len(rows):
    raise SystemExit('Invalid camera count or duplicate camera IDs')
for camera in rows:
    validate_saved_preprocessing(camera, base(camera['id']) / 'anomaly')
registry.write_text(json.dumps({'cameras': rows}))
registry.chmod(0o600)
if Path('runtime/camera-hub/enabled').exists():
    from app.cameras.stream_hub import StreamHub
    StreamHub('runtime/camera-hub/mediamtx.yml', 'config/camera-hub.example.json',
              'rtsp://127.0.0.1:8554').sync(rows)
# camera-media rewrites this list on start; write it now so camera-analysis never starts without one.
container = lambda c: '/data/' + ('pi' if c['id'] == 'pi' else 'extra/' + c['id'])
analysis = [dict(id=c['id'], data=container(c) + '/anomaly', control=container(c) + '/control',
                 preview='http://127.0.0.1:8082/view/' + c['id'] + '/api/live.jpg',
                 recording_mode='anomaly', settings=c['settings']) for c in rows]
Path('runtime/camera-manager/analysis.json').write_text(json.dumps(analysis))
print(int(any(c['capture_url'] for c in rows)))
PY
)"

if [[ "$pi_registered" == "1" ]]; then
  if [[ "$pi_tools" == "1" ]]; then
    "${SCRIPT_DIR}/pi-camera-host-setup.sh"
  else
    echo "WARNING: the registry contains the Pi camera, but this host has no Pi camera tools." >&2
  fi
fi

camera_services=(camera-media camera-analysis)
if [[ -f runtime/camera-hub/enabled ]]; then
  camera_services=(camera-hub "${camera_services[@]}")
fi
bms_ensure_camera_onnx_runtime
# Build while the running cameras keep recording.
"${COMPOSE[@]}" --profile cameras build camera-media camera-analysis service-controller
# Containers of the former one-container-per-role layout hold the same camera directories.
for name in camera-dashboard camera-recorder pi-camera-dashboard pi-camera-recorder; do
  if docker container inspect "$name" >/dev/null 2>&1; then
    docker rm -f "$name" >/dev/null
    echo ">>> Removed former container $name"
  fi
done
"${COMPOSE[@]}" --profile cameras up -d --no-deps --no-build "${camera_services[@]}" service-controller
bms_verify_services_created "${camera_services[@]}"

# nginx reads its config from a file bind mount, which keeps the old inode after an
# atomic replacement: recreate the container when the config or its mounts changed.
host_config_hash="$(sha256sum dashboard/nginx/default.conf | cut -d ' ' -f 1)"
container_config_hash="$(docker exec dashboard sha256sum /etc/nginx/conf.d/default.conf 2>/dev/null | cut -d ' ' -f 1 || true)"
if [[ "$host_config_hash" != "$container_config_hash" ]] \
    || docker inspect dashboard --format '{{range .Mounts}}{{.Destination}} {{end}}' 2>/dev/null | grep -q '/etc/nginx/cameras'; then
  "${COMPOSE[@]}" up -d --no-deps --force-recreate dashboard
fi
"${COMPOSE[@]}" exec -T dashboard nginx -t
"${COMPOSE[@]}" exec -T dashboard nginx -s reload
echo '>>> Cameras: http://<host>/cameras/'
