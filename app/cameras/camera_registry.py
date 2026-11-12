"""Registry of additional cameras and the files generated from it.

The two original cameras (`cameras` and `pi-camera` profiles) are defined by hand in
docker-compose.yml and keep their names. Every further camera is one entry in
`runtime/cameras.json`; from the registry this module writes

* `runtime/docker-compose.cameras.yml`: a recorder and a dashboard per camera, plus the
  mounts the shared analysis worker needs for each camera,
* `runtime/camera-registry/analysis-extra.json`: the cameras the shared worker analyses,
* `runtime/camera-registry/nginx/cameras.conf`: the `/cameras/<id>/` routes.

It uses only the standard library so it can run on the host:
    python3 app/cameras/camera_registry.py generate [--root DIR]
"""
import argparse
import json
from pathlib import Path
import re
import sys

try:
    from .files import write_text
except ImportError:
    from files import write_text

CAMERA_ID = re.compile(r'[a-z0-9][a-z0-9-]{0,15}')
ENVIRONMENT_NAME = re.compile(r'[A-Z][A-Z0-9_]{0,63}')
# Names taken by the hand-written cameras and by routes of the stack.
RESERVED_IDS = frozenset({'wifi', 'pi', 'cameras', 'pi-camera', 'analysis', 'api', 'probe'})
FIRST_PORT, LAST_PORT = 8085, 8199
RESERVED_PORTS = frozenset({8082, 8083, 8084, 8086})  # InfluxDB also publishes a host port.
RECORDING_MODES = ('continuous', 'anomaly')
MODEL_OPTIONS = {'jpeg_draft': (0, 1), 'top_k': (1, 20), 'coreset_size': (256, 4096), 'threads': (1, 4)}
ENTRY_KEYS = {'id', 'label', 'url_env', 'port', 'preview_path', 'recording_mode', 'settings'}
SETTING_KEYS = {'interval_sec', 'confirm_frames', 'normal_frames', 'model'}

COMPOSE_FILE = 'runtime/docker-compose.cameras.yml'
REGISTRY_FILE = 'runtime/cameras.json'
EXTRA_DIR = 'runtime/cameras-extra'
OUTPUT_DIR = 'runtime/camera-registry'
PROFILE = 'cameras-extra'


def validate_registry(data):
    """Return the validated camera entries with a port assigned to every camera."""
    cameras = data.get('cameras') if isinstance(data, dict) else None
    if not isinstance(cameras, list):
        raise ValueError('The registry must be an object with a "cameras" list')
    seen, ports, result = set(), set(RESERVED_PORTS), []
    for entry in cameras:
        if not isinstance(entry, dict):
            raise ValueError('Every camera must be an object')
        unknown = set(entry) - ENTRY_KEYS
        if unknown:
            raise ValueError(f'Unknown camera field(s): {", ".join(sorted(unknown))}')
        identifier = entry.get('id')
        if not isinstance(identifier, str) or not CAMERA_ID.fullmatch(identifier):
            raise ValueError('Camera id must be 1-16 lowercase letters, digits or dashes')
        if identifier in RESERVED_IDS or identifier in seen:
            raise ValueError(f'Camera id is reserved or duplicated: {identifier}')
        seen.add(identifier)
        label = entry.get('label', identifier)
        if not isinstance(label, str) or not 1 <= len(label) <= 40 or any(ord(c) < 32 for c in label):
            raise ValueError(f'Camera {identifier}: label must be 1-40 printable characters')
        url_env = entry.get('url_env')
        if not isinstance(url_env, str) or not ENVIRONMENT_NAME.fullmatch(url_env):
            raise ValueError(f'Camera {identifier}: url_env must name an environment variable, for example CAMERA_GARAGE_URL')
        preview = entry.get('preview_path', '')
        if preview and (not isinstance(preview, str) or not preview.startswith('/') or any(c in preview for c in ' ?#"\'')):
            raise ValueError(f'Camera {identifier}: preview_path must be an absolute URL path')
        mode = entry.get('recording_mode')
        if mode is not None and mode not in RECORDING_MODES:
            raise ValueError(f'Camera {identifier}: recording_mode must be continuous or anomaly')
        settings = entry.get('settings', {})
        if not isinstance(settings, dict) or set(settings) - SETTING_KEYS:
            raise ValueError(f'Camera {identifier}: unsupported settings')
        for key, low, high in (('interval_sec', .2, 60), ('confirm_frames', 1, 5), ('normal_frames', 20, 120)):
            if key in settings and (isinstance(settings[key], bool) or not isinstance(settings[key], (int, float))
                                    or not low <= settings[key] <= high):
                raise ValueError(f'Camera {identifier}: {key} must be between {low} and {high}')
        model = settings.get('model', {})
        if not isinstance(model, dict) or set(model) - set(MODEL_OPTIONS):
            raise ValueError(f'Camera {identifier}: unsupported model options')
        for key, (low, high) in MODEL_OPTIONS.items():
            if key in model and (isinstance(model[key], bool) or not isinstance(model[key], int) or not low <= model[key] <= high):
                raise ValueError(f'Camera {identifier}: model {key} must be between {low} and {high}')
        port = entry.get('port')
        if port is not None and (isinstance(port, bool) or not isinstance(port, int) or not FIRST_PORT <= port <= LAST_PORT):
            raise ValueError(f'Camera {identifier}: port must be between {FIRST_PORT} and {LAST_PORT}')
        if port in ports:
            raise ValueError(f'Camera {identifier}: port {port} is already used')
        if port is not None:
            ports.add(port)
        result.append(dict(entry, label=label))
    for entry in result:
        if entry.get('port') is None:
            entry['port'] = next(port for port in range(FIRST_PORT, LAST_PORT + 1) if port not in ports)
            ports.add(entry['port'])
    return result


def quote(value):
    """A JSON string is also a valid YAML double-quoted scalar."""
    return json.dumps(value)


def service_names(identifier):
    return f'cam-{identifier}-recorder', f'cam-{identifier}-dashboard'


def camera_environment(entry):
    mode = entry.get('recording_mode')
    return [
        ('TZ', 'UTC'),
        ('CAMERA_URL', '${%s:-}' % entry['url_env']),
        ('CAMERA_NAME', entry['label']),
        ('CAMERA_DESCRIPTION', 'RTSP camera'),
        ('RECORDING_DIR', '/recordings'),
        ('CLIP_SECONDS', '${CAMERA_CLIP_SECONDS:-60}'),
        ('CAMERA_RECORDING_MODE', mode if mode else '${CAMERA_RECORDING_MODE:-continuous}'),
        ('CAMERA_EVENT_PRE_SEC', '${CAMERA_EVENT_PRE_SEC:-7}'),
        ('CAMERA_EVENT_POST_SEC', '${CAMERA_EVENT_POST_SEC:-10}'),
        ('MAX_STORAGE_MB', '${CAMERA_MAX_STORAGE_MB:-10240}'),
        ('MAX_AGE_HOURS', '${CAMERA_MAX_AGE_HOURS:-24}'),
    ]


def render_compose(cameras):
    lines = ['# Generated by app/cameras/camera_registry.py from runtime/cameras.json. Do not edit.', 'services:']
    analysis_volumes = []
    for entry in cameras:
        identifier, port = entry['id'], entry['port']
        recorder, dashboard = service_names(identifier)
        base = f'./{EXTRA_DIR}/{identifier}'
        labels = [f'bms.camera.id={identifier}', f'bms.camera.label={entry["label"]}', f'bms.camera.port={port}']
        environment = camera_environment(entry)
        for name, role in ((recorder, 'recorder'), (dashboard, 'dashboard')):
            lines += ['', f'  {name}:', f'    container_name: {name}', '    image: bms-cameras:local', f'    profiles: [{PROFILE}]',
                      '    network_mode: host', '    init: true', '    restart: unless-stopped']
            if role == 'recorder':
                lines.append('    stop_grace_period: 20s')
            else:
                lines.append('    command: [python, dashboard.py]')
            lines.append('    cpuset: ${CAMERA_MEDIA_CPUSET:-}')
            lines.append('    labels:')
            lines += [f'      - {quote(label)}' for label in labels + [f'bms.camera.role={role}']]
            lines.append('    environment:')
            lines += [f'      {key}: {quote(value)}' for key, value in environment]
            if role == 'dashboard':
                lines += ['      DASHBOARD_HOST: 0.0.0.0', f'      DASHBOARD_PORT: {quote(str(port))}']
                if entry.get('preview_path'):
                    lines.append(f'      CAMERA_PREVIEW_PATH: {quote(entry["preview_path"])}')
            anomaly = ':/anomaly' if role == 'dashboard' else ':/anomaly:ro'
            lines += ['    volumes:', f'      - {base}/recordings:/recordings',
                      f'      - {base}/control:/control', f'      - {base}/anomaly{anomaly}']
            if role == 'dashboard':
                lines += ['    depends_on:', f'      {recorder}:', '        condition: service_started',
                          '    healthcheck:',
                          f'      test: [CMD, python, -c, "import urllib.request; urllib.request.urlopen(\'http://127.0.0.1:{port}/api/status\', timeout=3).read()"]',
                          '      interval: 30s', '      timeout: 5s', '      retries: 3']
        analysis_volumes += [f'{base}/anomaly:/anomaly/{identifier}', f'{base}/control:/control/{identifier}']
    if cameras:
        lines += ['', '  # The shared analysis worker needs each extra camera\'s directories.', '  camera-analysis:',
                  '    volumes:'] + [f'      - {volume}' for volume in analysis_volumes]
    return '\n'.join(lines) + '\n'


def analysis_entries(cameras):
    result = []
    for entry in cameras:
        item = dict(id=entry['id'], data=f'/anomaly/{entry["id"]}', control=f'/control/{entry["id"]}',
                    preview=f'http://127.0.0.1:{entry["port"]}/api/live.jpg')
        if entry.get('recording_mode'):
            item['recording_mode'] = entry['recording_mode']
        if entry.get('settings'):
            item['settings'] = entry['settings']
        result.append(item)
    return result


def render_nginx(cameras):
    lines = ['# Generated by app/cameras/camera_registry.py from runtime/cameras.json. Do not edit.']
    for entry in cameras:
        identifier, port = entry['id'], entry['port']
        lines += [
            '', f'location = /cameras/{identifier} {{', f'    return 301 /cameras/{identifier}/;', '}',
            '', f'location /cameras/{identifier}/ {{',
            f'    rewrite ^/cameras/{identifier}/(.*)$ /$1 break;',
            '    proxy_set_header Host $host;',
            '    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;',
            '    proxy_set_header X-Forwarded-Proto $scheme;',
            '    proxy_buffering off;', '    proxy_read_timeout 60s;',
            f'    proxy_pass http://host.docker.internal:{port};', '}']
    return '\n'.join(lines) + '\n'


def write_if_changed(path, text):
    if path.exists() and path.read_text() == text:
        return False
    write_text(path, text)
    return True


def load_registry(root):
    """Read and validate runtime/cameras.json (missing file: no extra cameras)."""
    registry = Path(root) / REGISTRY_FILE
    try:
        data = json.loads(registry.read_text()) if registry.exists() else {'cameras': []}
    except ValueError as error:
        raise ValueError(f'{REGISTRY_FILE} is not valid JSON') from error
    return validate_registry(data)


def generate(root):
    """Write the generated files under `root`; returns (cameras, changed file names)."""
    root = Path(root)
    cameras = load_registry(root)
    for entry in cameras:
        for directory in ('recordings', 'control', 'anomaly'):
            (root / EXTRA_DIR / entry['id'] / directory).mkdir(parents=True, exist_ok=True)
    (root / OUTPUT_DIR / 'nginx').mkdir(parents=True, exist_ok=True)
    outputs = {
        COMPOSE_FILE: render_compose(cameras),
        f'{OUTPUT_DIR}/analysis-extra.json': json.dumps(analysis_entries(cameras), indent=2) + '\n',
        f'{OUTPUT_DIR}/nginx/cameras.conf': render_nginx(cameras),
    }
    if not cameras:
        # No extra cameras: leave no Compose override behind.
        (root / COMPOSE_FILE).unlink(missing_ok=True)
        outputs.pop(COMPOSE_FILE)
    changed = [name for name, text in outputs.items() if write_if_changed(root / name, text)]
    return cameras, changed


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('command', choices=('generate', 'list'))
    parser.add_argument('--root', default=str(Path(__file__).resolve().parents[2]))
    arguments = parser.parse_args(argv)
    try:
        if arguments.command == 'list':
            for entry in load_registry(arguments.root):
                print(f'{entry["id"]}\t{entry["port"]}\t{entry["label"]}')
        else:
            cameras, changed = generate(arguments.root)
            print(f'{len(cameras)} extra camera(s); updated: {", ".join(changed) or "nothing"}')
    except (OSError, ValueError) as error:
        print(f'ERROR: {error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
