"""One camera UI and supervisor with independent camera processes and persistent controls."""
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import html
import json
import logging
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import threading
import time
from urllib.parse import urlsplit
import uuid

try:
    from .files import read_json, write_json
    from .stream_hub import StreamHub
except ImportError:
    from files import read_json, write_json
    from stream_hub import StreamHub

LOG = logging.getLogger('camera-manager')
ROOT = Path(__file__).resolve().parent
WEB = ROOT / 'web'
CAMERA_ID = re.compile(r'[a-z0-9][a-z0-9-]{0,15}')
# Camera IDs that would collide with routes of this server.
RESERVED = {'api', 'view', 'manager', 'static'}
# Per-camera PatchCoreLite options and their allowed ranges.
MODEL_OPTIONS = {'jpeg_draft': (0, 1), 'top_k': (1, 20), 'coreset_size': (256, 4096), 'threads': (1, 4)}
MAX_CAMERAS = 16
ASSETS = {'fleet.js': 'application/javascript', 'fleet.css': 'text/css', 'style.css': 'text/css'}


def validate_saved_preprocessing(camera, anomaly_directory):
    """Reject a migration mismatch before a worker can invalidate saved learning."""
    catalog = read_json(Path(anomaly_directory) / 'learning' / 'catalog.json', {})
    if not catalog.get('sessions'):
        return
    saved = catalog.get('configuration', {})
    model = camera.get('settings', {}).get('model', {})
    if (saved.get('jpeg_draft') != bool(model.get('jpeg_draft', 0))
            or saved.get('score_top_k') != model.get('top_k', 1)):
        raise ValueError('Saved normal sessions require their original JPEG draft and Top-K settings')


def validate_camera(value):
    """Validate only public camera fields; state and storage paths are server-owned."""
    if not isinstance(value, dict) or set(value) - {'id', 'label', 'url', 'capture_url', 'enabled', 'settings'}:
        raise ValueError('Unsupported camera fields')
    identifier = value.get('id')
    if not isinstance(identifier, str) or not CAMERA_ID.fullmatch(identifier) or identifier in RESERVED:
        raise ValueError('Camera ID must be 1-16 lowercase letters, digits or dashes')
    label = value.get('label', identifier)
    if not isinstance(label, str) or not 1 <= len(label.strip()) <= 40 or any(ord(c) < 32 for c in label):
        raise ValueError('Camera name must be 1-40 printable characters')
    url = value.get('url', '')
    if not isinstance(url, str) or len(url) > 2048 or any(ord(c) < 32 for c in url):
        raise ValueError('Invalid stream URL')
    parsed = urlsplit(url)
    try:
        parsed.port
    except ValueError:
        raise ValueError('Invalid stream port') from None
    local_pi = parsed.scheme == 'http' and parsed.hostname == '127.0.0.1' and parsed.path == '/stream.ts'
    if not ((parsed.scheme == 'rtsp' and parsed.hostname) or local_pi) or parsed.fragment:
        raise ValueError('Use an RTSP stream or the local Pi camera stream')
    capture = value.get('capture_url', '')
    if capture and (not local_pi or capture != 'http://127.0.0.1:8084'):
        raise ValueError('Remote capture settings are not supported yet')
    enabled = value.get('enabled', True)
    if not isinstance(enabled, bool):
        raise ValueError('Enabled must be true or false')
    settings = value.get('settings', {})
    if not isinstance(settings, dict) or set(settings) - {'interval_sec', 'confirm_frames', 'normal_frames', 'model'}:
        raise ValueError('Invalid camera analysis settings')
    for name, low, high in (('interval_sec', .2, 60), ('confirm_frames', 1, 5), ('normal_frames', 20, 120)):
        if name in settings:
            number = settings[name]
            if isinstance(number, bool) or not isinstance(number, (float, int)) or not low <= number <= high or (name != 'interval_sec' and int(number) != number):
                raise ValueError('Invalid camera analysis setting')
    model = settings.get('model', {})
    if not isinstance(model, dict) or set(model) - set(MODEL_OPTIONS):
        raise ValueError('Invalid camera model settings')
    for name, number in model.items():
        low, high = MODEL_OPTIONS[name]
        if isinstance(number, bool) or not isinstance(number, int) or not low <= number <= high:
            raise ValueError('Invalid camera model setting')
    return dict(id=identifier, label=label.strip(), url=url, capture_url=capture, enabled=enabled,
                settings=settings)


class CameraRegistry:
    def __init__(self, directory, data_root, public_url):
        self.directory, self.data_root = Path(directory), Path(data_root)
        self.public_url = public_url.rstrip('/')
        self.path = self.directory / 'cameras.json'
        self.analysis_dirty = False
        self.lock = threading.RLock()
        if not self.path.exists():
            raise ValueError('Initialize the shared camera registry before starting')
        data = json.loads(self.path.read_text())
        if not isinstance(data, dict) or set(data) != {'cameras'} or not isinstance(data['cameras'], list):
            raise ValueError('Invalid shared camera registry')
        self.cameras = [validate_camera(row) for row in data['cameras']]
        if len(self.cameras) > MAX_CAMERAS or len({c['id'] for c in self.cameras}) != len(self.cameras):
            raise ValueError('Too many cameras or duplicate camera IDs')
        for camera in self.cameras:
            validate_saved_preprocessing(camera, self.paths(camera['id'])['anomaly'])
            self.prepare(camera)
        self.path.chmod(0o600)
        self.publish_analysis()

    def paths(self, identifier):
        # The built-in Pi camera keeps its original directory; every other camera lives under extra/.
        base = self.data_root / 'pi' if identifier == 'pi' else self.data_root / 'extra' / identifier
        return dict(base=base, control=base / 'control', anomaly=base / 'anomaly', recordings=base / 'recordings')

    def prepare(self, camera):
        paths = self.paths(camera['id'])
        for key in ('control', 'anomaly', 'recordings'):
            paths[key].mkdir(parents=True, exist_ok=True)
        marker = paths['control'] / 'camera-stopped'
        if camera['enabled']:
            marker.unlink(missing_ok=True)
        else:
            marker.touch()

    def publish_analysis(self):
        entries = [dict(id=c['id'], data=str(self.paths(c['id'])['anomaly']),
                        control=str(self.paths(c['id'])['control']),
                        preview=f"{self.public_url}/view/{c['id']}/api/live.jpg",
                        recording_mode='anomaly', settings=c['settings']) for c in self.cameras]
        write_json(self.directory / 'analysis.json', entries)
        self.analysis_dirty = False

    def persist(self):
        # This runtime file can contain RTSP credentials; make every replacement private.
        temporary = self.path.with_name('cameras.' + uuid.uuid4().hex + '.tmp')
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, 'w') as stream:
                json.dump(dict(cameras=self.cameras), stream)
            temporary.replace(self.path)
        finally:
            temporary.unlink(missing_ok=True)

    def get(self, identifier):
        with self.lock:
            for camera in self.cameras:
                if camera['id'] == identifier:
                    return dict(camera)
        raise KeyError(identifier)

    def add(self, data):
        camera = validate_camera(data)
        if camera['id'] == 'pi' and not camera['capture_url']:
            raise ValueError('The ID pi is reserved for the built-in camera; choose another ID')
        with self.lock:
            if len(self.cameras) >= MAX_CAMERAS:
                raise ValueError('Camera limit reached')
            if any(c['id'] == camera['id'] for c in self.cameras):
                raise ValueError('Camera ID already exists')
            validate_saved_preprocessing(camera, self.paths(camera['id'])['anomaly'])
            self.prepare(camera)
            self.cameras.append(camera)
            try:
                self.persist()
            except OSError:
                self.cameras.pop()
                raise
            self.analysis_dirty = True
            try:
                self.publish_analysis()
            except OSError:
                LOG.warning('Camera saved; analysis configuration write will be retried')
        return camera

    def set_enabled(self, identifier, enabled):
        with self.lock:
            camera = next((c for c in self.cameras if c['id'] == identifier), None)
            if camera is None:
                raise KeyError(identifier)
            old = camera['enabled']
            camera['enabled'] = enabled
            try:
                self.persist()
            except OSError:
                camera['enabled'] = old
                raise
            self.prepare(camera)
            return dict(camera)

    def validate_edit(self, identifier, changes):
        original = self.get(identifier)
        if not isinstance(changes, dict) or set(changes) - {'label', 'url'}:
            raise ValueError('Only camera name and RTSP URL can be edited')
        local = urlsplit(original['url']).scheme != 'rtsp'
        if 'url' in changes and local:
            raise ValueError('The built-in camera source cannot be changed')
        updated = validate_camera(dict(original, **changes))
        if not local and urlsplit(updated['url']).scheme != 'rtsp':
            raise ValueError('Use an RTSP stream for a remote camera')
        return updated

    def modify(self, identifier, changes=None):
        """Update a stopped camera or unregister it, preserving its data directories."""
        with self.lock:
            original = self.get(identifier)
            if original['enabled']:
                raise ValueError('Stop this camera before editing or deleting it')
            if changes is not None:
                updated = self.validate_edit(identifier, changes)
            previous = self.cameras
            if changes is None:
                self.cameras = [c for c in previous if c['id'] != identifier]
            else:
                self.cameras = [updated if c['id'] == identifier else c for c in previous]
            try:
                self.persist()
            except OSError:
                self.cameras = previous
                raise
            self.analysis_dirty = True
            try:
                self.publish_analysis()
            except OSError:
                LOG.warning('Camera saved; analysis configuration write will be retried')
            return dict(updated) if changes is not None else original


class CameraManager:
    def __init__(self, registry, spawn=subprocess.Popen, clock=time.monotonic, hub=None):
        self.registry, self.spawn, self.clock = registry, spawn, clock
        self.hub = hub
        self.jobs = {}
        self.retry_at = {}
        self.lock = threading.RLock()
        self.stop = threading.Event()
        self.action_locks = {}

    def environment(self, camera):
        paths = self.registry.paths(camera['id'])
        environment = dict(os.environ)
        # Do not inherit a different camera's URL or substream configuration.
        environment.update(CAMERA_URL=self.hub.source_url(camera) if self.hub else camera['url'], CAMERA_CAPTURE_URL=camera['capture_url'],
                           CAMERA_PREVIEW_PATH='', CAMERA_NAME=camera['label'],
                           CAMERA_DESCRIPTION='Local CSI camera' if camera['capture_url'] else 'Remote H.264 camera',
                           RECORDING_DIR=str(paths['recordings']), CAMERA_CONTROL_DIR=str(paths['control']),
                           CAMERA_ANOMALY_DIR=str(paths['anomaly']), CAMERA_RECORDING_MODE='anomaly',
                           CAMERA_SPARSE_PREVIEW='1',
                           DASHBOARD_HOST='127.0.0.1', DASHBOARD_PORT='0',
                           DASHBOARD_PORT_FILE=str(paths['control'] / 'dashboard-port.json'))
        return environment

    @staticmethod
    def terminate(process):
        """Stop an isolated camera process group, including its FFmpeg children."""
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        try:
            process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            pass
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=3)

    def stop_jobs(self, identifier):
        for role in ('dashboard', 'recorder'):
            with self.lock:
                process = self.jobs.pop((identifier, role), None)
            if process is not None:
                self.terminate(process)

    def reconcile(self):
        with self.lock:
            with self.registry.lock:
                if self.registry.analysis_dirty:
                    try:
                        self.registry.publish_analysis()
                    except OSError:
                        pass
                cameras = [dict(c) for c in self.registry.cameras]
            if self.hub:
                try:
                    self.hub.sync(cameras)
                except OSError:
                    LOG.warning('Could not update stream hub configuration; retrying')
                    return
            for camera in cameras:
                identifier = camera['id']
                if not camera['enabled']:
                    continue
                for role in ('recorder', 'dashboard'):
                    key = (identifier, role)
                    process = self.jobs.get(key)
                    if process is not None and process.poll() is None:
                        continue
                    if process is not None:
                        self.terminate(process)
                        self.jobs.pop(key, None)
                    if self.clock() < self.retry_at.get(key, 0):
                        continue
                    try:
                        self.jobs[key] = self.spawn([sys.executable, str(ROOT / f'{role}.py')],
                                                   env=self.environment(camera), start_new_session=True,
                                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    except OSError:
                        LOG.warning('Could not start %s for camera %s', role, identifier)
                    self.retry_at[key] = self.clock() + 5

    def action(self, identifier, action):
        if action not in ('start', 'stop', 'restart'):
            raise ValueError('Unknown camera action')
        with self.lock:
            action_lock = self.action_locks.setdefault(identifier, threading.RLock())
        with action_lock:
            camera = self.registry.get(identifier)
            if action in ('stop', 'restart'):
                self.registry.set_enabled(identifier, False)
                paths = self.registry.paths(identifier)
                session = read_json(paths['anomaly'] / 'session.json', {})
                write_json(paths['anomaly'] / 'command.json', dict(id=uuid.uuid4().hex, action='pause',
                           multiplier=session.get('multiplier', 1.3)))
                write_json(paths['control'] / 'state.json', dict(state='paused', updated=time.time()))
                self.stop_jobs(identifier)
            if action in ('start', 'restart'):
                self.registry.set_enabled(identifier, True)
                for role in ('dashboard', 'recorder'):
                    self.retry_at.pop((identifier, role), None)
                self.reconcile()
            return self.public_camera(self.registry.get(identifier))

    def modify(self, identifier, changes=None):
        # Serialize with start/stop; validation happens before stopping a running camera.
        with self.lock:
            action_lock = self.action_locks.setdefault(identifier, threading.RLock())
        with action_lock:
            self.registry.get(identifier)
            if changes is not None:
                self.registry.validate_edit(identifier, changes)
            self.action(identifier, 'stop')
            camera = self.registry.modify(identifier, changes)
            self.reconcile()
            return self.public_camera(camera) if changes is not None else dict(success=True, id=identifier)

    def dashboard_port(self, identifier):
        with self.lock:
            camera = self.registry.get(identifier)
            if not camera['enabled']:
                return None
            process = self.jobs.get((identifier, 'dashboard'))
            info = read_json(self.registry.paths(identifier)['control'] / 'dashboard-port.json', {})
            if process is None or process.poll() is not None or info.get('pid') != process.pid:
                return None
            port = info.get('port')
            return port if isinstance(port, int) and 0 < port < 65536 else None

    def public_camera(self, camera):
        """Return state without stream URLs or credentials."""
        identifier = camera['id']
        paths = self.registry.paths(identifier)
        analysis = read_json(paths['anomaly'] / 'status.json', {})
        recorder = read_json(paths['control'] / 'state.json', {})
        running = camera['enabled'] and all((p := self.jobs.get((identifier, role))) is not None
                                           and p.poll() is None for role in ('dashboard', 'recorder'))
        state = 'running' if running else 'starting' if camera['enabled'] else 'stopped'
        analysis_alive = time.time() - analysis.get('updated', 0) < 30
        return dict(id=identifier, label=camera['label'], enabled=camera['enabled'], state=state,
                    source_kind='rtsp' if urlsplit(camera['url']).scheme == 'rtsp' else 'local',
                    target=f'cam-{identifier}', path=f'/cameras/{identifier}/',
                    status_url=f'/cameras/{identifier}/api/status',
                    services={'camera-media': 'running' if running else 'exited',
                              'camera-analysis': 'running' if analysis_alive else 'missing'},
                    analysis_state=analysis.get('state', 'offline') if analysis_alive else 'offline',
                    recording_state=recorder.get('state', 'offline'))

    def snapshot(self):
        with self.lock, self.registry.lock:
            cameras = [self.public_camera(c) for c in self.registry.cameras]
        return dict(success=True, cameras=cameras,
                    manual_pauses={c['target']: True for c in cameras if not c['enabled']})

    def run(self):
        try:
            while not self.stop.is_set():
                self.reconcile()
                self.stop.wait(.5)
        finally:
            with self.lock:
                for identifier in {key[0] for key in self.jobs}:
                    self.stop_jobs(identifier)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    @property
    def manager(self):
        return self.server.manager

    def send(self, data, content_type='application/json', code=200, headers=None):
        if not isinstance(data, bytes):
            data = json.dumps(data).encode()
        self.send_response(code)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(data)

    def proxy(self, identifier, path):
        self.manager.registry.get(identifier)
        port = self.manager.dashboard_port(identifier)
        if port is None and path == '/':
            deadline = time.monotonic() + 3
            while port is None and time.monotonic() < deadline:
                if not self.manager.registry.get(identifier)['enabled']:
                    break
                time.sleep(.05)
                port = self.manager.dashboard_port(identifier)
        if port is None:
            if urlsplit(path).path == '/api/status':
                camera = self.manager.registry.get(identifier)
                return self.send(dict(camera_name=camera['label'], service='paused', recording_enabled=False,
                                      recording_mode='anomaly', healthy=False, analysis_state='paused',
                                      monitoring_enabled=False, clips=[], preview_quality=dict(state='idle')))
            return self.send(dict(error='Camera is stopped or starting'), code=503)
        length = int(self.headers.get('Content-Length', '0'))
        if not 0 <= length <= 65536:
            raise ValueError('Request is too large')
        body = self.rfile.read(length) if length else None
        connection = HTTPConnection('127.0.0.1', port, timeout=35)
        try:
            headers = {key: self.headers[key] for key in ('Content-Type', 'X-Camera-Control', 'Range') if key in self.headers}
            connection.request(self.command, path, body=body, headers=headers)
            response = connection.getresponse()
            self.send_response(response.status)
            for key, value in response.getheaders():
                if key.lower() in ('content-type', 'content-length', 'content-range', 'accept-ranges', 'content-disposition') or key.lower().startswith('x-camera-'):
                    self.send_header(key, value)
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            while chunk := response.read(65536):
                self.wfile.write(chunk)
        finally:
            connection.close()

    def dispatch(self):
        path = urlsplit(self.path).path
        if self.command == 'GET' and path in ('/api/cameras', '/api/status'):
            return self.send(self.manager.snapshot())
        if self.command == 'GET' and path.lstrip('/') in ASSETS:
            name = path.lstrip('/')
            return self.send((WEB / name).read_bytes(), ASSETS[name])
        if self.command == 'GET' and re.fullmatch(r'/[a-z0-9-]+/(fleet.js|fleet.css|style.css)', path):
            name = path.rsplit('/', 1)[-1]
            return self.send((WEB / name).read_bytes(), ASSETS[name])
        if self.command == 'POST' and path.startswith('/api/cameras'):
            if self.headers.get('X-Camera-Control') != '1':
                return self.send(dict(error='Camera control header required'), code=403)
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 <= length <= 4096:
                raise ValueError('Request is too large')
            data = json.loads(self.rfile.read(length) or b'{}')
            if not isinstance(data, dict):
                raise ValueError('Camera settings must be an object')
            if path == '/api/cameras':
                camera = self.manager.registry.add(data)
                self.manager.reconcile()
                return self.send(self.manager.public_camera(camera), code=201)
            match = re.fullmatch(r'/api/cameras/([a-z0-9-]+)/([a-z]+)', path)
            if not match:
                raise KeyError(path)
            if match[2] in ('edit', 'delete'):
                if match[2] == 'delete' and data != {}:
                    raise ValueError('Delete does not accept camera settings')
                return self.send(self.manager.modify(match[1], data if match[2] == 'edit' else None))
            return self.send(self.manager.action(*match.groups()))
        match = re.fullmatch(r'/view/([a-z0-9-]+)/(.*)', path)
        if match:
            target = '/' + match[2]
            if urlsplit(self.path).query:
                target += '?' + urlsplit(self.path).query
            return self.proxy(match[1], target)
        match = re.fullmatch(r'/([a-z0-9-]+)/(api/.*)', path)
        if match:
            target = '/' + match[2]
            if urlsplit(self.path).query:
                target += '?' + urlsplit(self.path).query
            return self.proxy(match[1], target)
        if self.command == 'GET' and (path == '/' or re.fullmatch(r'/[a-z0-9-]+/', path)):
            base = self.headers.get('X-Forwarded-Prefix', '/')
            base = '/cameras/' if base == '/cameras/' else '/'
            body = (WEB / 'fleet.html').read_text().replace('__CAMERA_BASE__', html.escape(base, quote=True))
            return self.send(body.encode(), 'text/html')
        raise KeyError(path)

    def handle_request(self):
        try:
            self.dispatch()
        except KeyError:
            self.send(dict(error='Unknown camera or route'), code=404)
        except ValueError as error:
            self.send(dict(error=str(error)), code=400)
        except TypeError:
            self.send(dict(error='Invalid camera settings or request'), code=400)
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True
        except (ConnectionError, TimeoutError):
            self.send(dict(error='Camera service is unavailable'), code=503)
        except OSError:
            self.send(dict(error='Camera storage or service is unavailable'), code=503)

    do_GET = do_POST = handle_request


def main():
    logging.basicConfig(level=logging.INFO)
    port = int(os.environ.get('CAMERA_MEDIA_PORT', '8082'))
    registry = CameraRegistry(os.environ.get('CAMERA_MEDIA_REGISTRY', '/registry'),
                              os.environ.get('CAMERA_MEDIA_DATA', '/data'), f'http://127.0.0.1:{port}')
    hub = None
    if os.environ.get('CAMERA_HUB_CONFIG'):
        hub = StreamHub(os.environ['CAMERA_HUB_CONFIG'], os.environ['CAMERA_HUB_TEMPLATE'],
                        os.environ['CAMERA_HUB_URL'])
    manager = CameraManager(registry, hub=hub)
    server = ThreadingHTTPServer(('0.0.0.0', port), Handler)
    server.manager = manager
    worker = threading.Thread(target=manager.run)
    worker.start()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: threading.Thread(target=server.shutdown, daemon=True).start())
    try:
        server.serve_forever()
    finally:
        server.server_close()
        manager.stop.set()
        worker.join(timeout=40)


if __name__ == '__main__':
    main()
