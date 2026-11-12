#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/lib/compose-common.sh"
bms_cd_project
mkdir -p runtime/camera-manager runtime/cameras-extra runtime/pi-camera runtime/cameras
# Camera state and the private registry may be owned by container root.
sudo python3 - <<'PY'
import json, os, subprocess
from pathlib import Path
from app.cameras.camera_manager import validate_camera, validate_saved_preprocessing
for path in (Path('runtime/pi-camera/anomaly/status.json'), Path('runtime/cameras/anomaly/status.json')):
    if path.exists() and json.loads(path.read_text()).get('state') in ('learning', 'training'):
        raise SystemExit('Pause camera learning before migrating/restarting shared camera services')
p = Path('runtime/camera-manager/cameras.json')
if not p.exists():
    data = json.loads(Path('config/cameras-shared.example.json').read_text())
    if Path('runtime/pi-camera/control/capture.json').exists():
        data['cameras'].append(dict(id='pi', label='Raspberry Pi Camera', url='http://127.0.0.1:8084/stream.ts',
                                    capture_url='http://127.0.0.1:8084', enabled=(subprocess.run(
                                        ['docker','inspect','-f','{{.State.Running}}','pi-camera-dashboard'],
                                        capture_output=True,text=True).stdout.strip() == 'true')))
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as f:
        json.dump(data, f)
# Generate only the analysis list here; container paths are deliberately identical in both services.
rows = json.loads(p.read_text())['cameras']
# Preserve the exact per-camera model options used by the previous deployment.
result = subprocess.run(['docker','inspect','camera-analysis'],capture_output=True,text=True)
previous = {}
if result.returncode == 0:
    environment = json.loads(result.stdout)[0]['Config']['Env']
    raw = next((value.partition('=')[2] for value in environment if value.startswith('CAMERA_ANALYSIS_CAMERAS=')), '[]')
    previous = {camera['id']: camera.get('settings', {}) for camera in json.loads(raw)}
for camera in rows:
    if 'settings' not in camera and camera['id'] in previous:
        camera['settings'] = previous[camera['id']]

rows = [validate_camera(row) for row in rows]
if len(rows) > 16 or len({c['id'] for c in rows}) != len(rows):
    raise SystemExit('Invalid camera count or duplicate camera IDs')
for camera in rows:
    base = Path('runtime/pi-camera') if camera['id'] == 'pi' else Path('runtime/cameras') if camera['id'] == 'wifi' else Path('runtime/cameras-extra') / camera['id']
    validate_saved_preprocessing(camera, base / 'anomaly')
if Path('runtime/camera-hub/enabled').exists():
    from app.cameras.stream_hub import StreamHub
    StreamHub('runtime/camera-hub/mediamtx.yml', 'config/camera-hub.example.json',
              'rtsp://127.0.0.1:8554').sync(rows)
p.write_text(json.dumps({'cameras':rows}));p.chmod(0o600)
analysis = [dict(id=c['id'], data='/data/' + (c['id'] if c['id'] in ('pi','wifi') else 'extra/'+c['id']) + '/anomaly',
                 control='/data/' + (c['id'] if c['id'] in ('pi','wifi') else 'extra/'+c['id']) + '/control',
                 preview='http://127.0.0.1:8082/view/'+c['id']+'/api/live.jpg', recording_mode='anomaly', settings=c['settings']) for c in rows]
Path('runtime/camera-manager/analysis.json').write_text(json.dumps(analysis))
PY
# Build while the previous deployment is still available.
COMPOSE=(docker compose -f docker-compose.yml -f docker-compose.override.yml -f docker-compose.camera-shared.yml)
camera_services=(camera-media camera-analysis service-controller)
if [[ -f runtime/camera-hub/enabled ]]; then
  COMPOSE+=(-f docker-compose.camera-hub.yml)
  camera_services=(camera-hub "${camera_services[@]}")
fi
bms_ensure_camera_onnx_runtime
"${COMPOSE[@]}" --profile camera-shared build camera-media camera-analysis service-controller
# Preserve state while handing the camera directories over to the shared services.
for name in camera-dashboard camera-recorder pi-camera-dashboard pi-camera-recorder camera-analysis; do
  if docker container inspect "$name" >/dev/null 2>&1; then docker stop "$name" >/dev/null; fi
done
"${COMPOSE[@]}" --profile camera-shared up -d --no-deps --no-build "${camera_services[@]}"
cp config/nginx-cameras-shared.conf runtime/camera-registry/nginx/shared.conf
# A file bind mount retains its old inode after an atomic source-file replacement.
host_config_hash="$(sha256sum dashboard/nginx/default.conf | cut -d ' ' -f 1)"
container_config_hash="$(docker exec dashboard sha256sum /etc/nginx/conf.d/default.conf 2>/dev/null | cut -d ' ' -f 1 || true)"
if [[ "$host_config_hash" != "$container_config_hash" ]]; then
  "${COMPOSE[@]}" up -d --no-deps --force-recreate dashboard
fi
"${COMPOSE[@]}" exec -T dashboard nginx -t
"${COMPOSE[@]}" exec -T dashboard nginx -s reload
touch runtime/camera-manager/enabled
echo 'Shared camera workspace: http://<host>/cameras/'
