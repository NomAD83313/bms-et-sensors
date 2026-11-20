"""Small, IP-restricted capture-control API for a native Zero camera publisher."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
import os
from pathlib import Path
import tempfile
import threading

try:
    from .capture_settings import DEFAULTS, validate_update
except ImportError:
    from capture_settings import DEFAULTS, validate_update

RESOLUTIONS = ((640, 360), (1280, 720), (1920, 1080))
IMX415_MODE = '3864:2192:10:P'
CAPABILITIES = dict(device='bms-zero-camera', capture_modes=['video'], resolutions=[f'{w}x{h}' for w, h in RESOLUTIONS],
                    fps=[5, 10, 15], rotations=[0, 180],
                    note='Zero camera: video up to 1920 × 1080, with automatic exposure. '
                         'The 16:9 view is preserved when changing resolution. '
                         'Rotate 180° corrects a camera mounted upside down. '
                         'Applying settings pauses detection and event recording. '
                         'After changing resolution, check the image and learn a new normal view.')


class CaptureControl:
    def __init__(self, path):
        self.path = Path(path)
        self.lock = threading.RLock()

    def config(self):
        return json.loads(self.path.read_text())

    def allowed(self, address):
        peer = ipaddress.ip_address(address)
        return any(peer in ipaddress.ip_network(network) for user in self.config()['authInternalUsers']
                   if user['user'] == 'any' and any(p['action'] == 'read' for p in user['permissions'])
                   for network in user['ips'])

    def settings(self):
        with self.lock:
            camera = self.config()['paths']['cam']
            return dict(DEFAULTS, width=camera['rpiCameraWidth'], height=camera['rpiCameraHeight'],
                        fps=camera['rpiCameraFPS'], bitrate=camera['rpiCameraBitrate'],
                        rotation=180 if camera.get('rpiCameraHFlip') and camera.get('rpiCameraVFlip') else 0)

    def update(self, body):
        with self.lock:
            settings = validate_update(self.settings(), body)
            dimensions = settings['width'], settings['height']
            if (dimensions not in RESOLUTIONS or settings['fps'] not in CAPABILITIES['fps']
                    or settings['capture_mode'] != 'video' or settings['shutter_us'] != 0
                    or settings['snapshot_interval_ms'] != DEFAULTS['snapshot_interval_ms']):
                raise ValueError('Select video at 640x360, 1280x720 or 1920x1080 and 5, 10 or 15 FPS')
            config = self.config()
            camera = config['paths']['cam']
            mode = camera.get('rpiCameraMode')
            if mode != IMX415_MODE:
                mode = '2592:1944:10:P' if settings['width'] > 1296 else '1296:972:10:P'
            camera.update(rpiCameraWidth=settings['width'], rpiCameraHeight=settings['height'],
                          rpiCameraFPS=settings['fps'], rpiCameraIDRPeriod=int(settings['fps']),
                          rpiCameraMode=mode)
            # Both flips are a 180-degree rotation relative to the libcamera default,
            # which already compensates the device-tree mounting rotation.
            flipped = settings['rotation'] == 180
            camera.update(rpiCameraHFlip=flipped, rpiCameraVFlip=flipped)
            # The publisher watches this file and reloads the camera path automatically.
            # Rename within the same directory prevents partial configurations.
            with tempfile.NamedTemporaryFile(mode='w', dir=self.path.parent, delete=False) as stream:
                temporary = Path(stream.name)
                try:
                    json.dump(config, stream, indent=2)
                    stream.flush()
                    os.fsync(stream.fileno())
                    temporary.replace(self.path)
                finally:
                    temporary.unlink(missing_ok=True)
            return settings


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def send(self, body, code=200):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(data)

    def dispatch(self):
        try:
            if not self.server.control.allowed(self.client_address[0]):
                return self.send(dict(error='Capture control is restricted to the central host'), 403)
            if self.command == 'GET' and self.path == '/api/capabilities':
                return self.send(CAPABILITIES)
            if self.path != '/api/config':
                return self.send(dict(error='Unknown route'), 404)
            if self.command == 'GET':
                return self.send(self.server.control.settings())
            if self.headers.get('X-Camera-Control') != '1':
                return self.send(dict(error='Camera control header required'), 403)
            size = int(self.headers.get('Content-Length', '0'))
            if not 1 <= size <= 2048:
                raise ValueError('Invalid request size')
            return self.send(self.server.control.update(json.loads(self.rfile.read(size))))
        except (ValueError, TypeError) as error:
            self.send(dict(error=str(error)), 400)
        except OSError:
            self.send(dict(error='Camera configuration is unavailable'), 503)

    do_GET = do_POST = dispatch


def main():
    server = ThreadingHTTPServer(('0.0.0.0', 8084), Handler)
    server.control = CaptureControl(os.environ.get('BMS_ZERO_CONFIG', '/var/lib/bms-zero-camera/mediamtx.json'))
    server.serve_forever()


if __name__ == '__main__':
    main()
