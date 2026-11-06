"""Share one rpicam MPEG-TS encoder between local camera consumers."""
import logging
import json
import os
from pathlib import Path
import math
import queue
import signal
import subprocess
import threading
import time
import uuid

try:
    from .capture_settings import validate, validate_update, DEFAULTS
except ImportError:
    from capture_settings import validate, validate_update, DEFAULTS
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LOG = logging.getLogger('pi-camera')


SETTINGS = Path(os.environ.get('PI_CAMERA_SETTINGS_FILE', '/var/lib/bms-pi-camera/config.json'))


def mode():
    data = json.loads(SETTINGS.read_text()) if SETTINGS.exists() else {}
    override = Path(os.environ.get('PI_CAMERA_MODE_FILE', '/run/bms-pi-camera-mode.json'))
    if override.exists():
        data.update(json.loads(override.read_text()))
    return validate(data)


def command():
    settings = mode()
    snapshots = settings['capture_mode'] == 'snapshots'
    fps = 1000 / settings['snapshot_interval_ms'] if snapshots else settings['fps']
    args = ['rpicam-vid', '--nopreview', '--timeout', '0',
            '--width', str(settings['width']), '--height', str(settings['height']),
            '--framerate', str(float(fps))]
    if settings['shutter_us']:
        args += ['--shutter', str(settings['shutter_us'])]
    if snapshots:
        args += ['--codec', 'mjpeg', '--quality', '85']
    else:
        args += ['--codec', 'libav', '--libav-video-codec', 'libx264',
                 '--libav-video-codec-opts',
                 'preset=ultrafast;tune=zerolatency;g=' + str(math.ceil(fps)),
                 '--libav-format', 'mpegts', '--bitrate', str(settings['bitrate'])]
    return args + ['--output', '-']


class Broadcast:
    def __init__(self):
        self.lock = threading.Lock()
        self.clients = set()
        self.stop = threading.Event()
        self.reload = threading.Event()
        self.frame_condition = threading.Condition()
        self.frame = None
        self.frame_at = 0
        self.sequence = 0
        self.generation = uuid.uuid4().hex
        self.last_snapshot_request = float('-inf')

    def subscribe(self):
        with self.lock:
            if len(self.clients) >= 8:
                return None
            client = queue.Queue(maxsize=64)
            self.clients.add(client)
            return client

    def remove(self, client):
        with self.lock:
            self.clients.discard(client)

    def publish(self, chunk):
        with self.lock:
            for client in list(self.clients):
                try:
                    client.put_nowait(chunk)
                except queue.Full:
                    # Disconnect slow readers instead of dropping transport packets.
                    self.clients.remove(client)
                    while not client.empty():
                        client.get_nowait()
                    client.put_nowait(None)

    def has_clients(self):
        with self.lock:
            return bool(self.clients) or time.monotonic() - self.last_snapshot_request < 5

    def snapshot(self, after=None):
        with self.lock:
            self.last_snapshot_request = time.monotonic()
        deadline = time.monotonic() + 5
        with self.frame_condition:
            while self.frame is None or self.generation + '-' + str(self.sequence) == after or time.time() - self.frame_at > max(.5, mode()['snapshot_interval_ms'] / 500):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self.frame_condition.wait(remaining)
            return self.frame, self.frame_at, self.generation + '-' + str(self.sequence)

    def reconfigure(self, settings):
        SETTINGS.parent.mkdir(parents=True, exist_ok=True)
        temporary = SETTINGS.with_name('config.' + uuid.uuid4().hex + '.tmp')
        temporary.write_text(json.dumps(settings))
        temporary.replace(SETTINGS)
        with self.frame_condition:
            self.frame, self.frame_at = None, 0
            self.generation = uuid.uuid4().hex
        self.reload.set()

    def run(self):
        while not self.stop.is_set():
            if not self.has_clients():
                self.stop.wait(.25)
                continue
            process = None
            buffer = b''
            self.reload.clear()
            try:
                process = subprocess.Popen(command(), stdout=subprocess.PIPE)
                snapshots = mode()['capture_mode'] == 'snapshots'
                generation = self.generation
                while not self.stop.is_set() and not self.reload.is_set() and self.has_clients():
                    chunk = process.stdout.read1(188 * 64)
                    if not chunk or self.reload.is_set():
                        break
                    self.publish(chunk)
                    if snapshots:
                        buffer += chunk
                        while True:
                            start = buffer.find(b'\xff\xd8')
                            end = buffer.find(b'\xff\xd9', max(0, start))
                            if start < 0 or end < 0:
                                break
                            with self.frame_condition:
                                if generation != self.generation or self.reload.is_set():
                                    break
                                self.frame = buffer[start:end+2]
                                self.frame_at = time.time()
                                self.sequence += 1
                                self.frame_condition.notify_all()
                            buffer = buffer[end+2:]
                        if len(buffer) > 16*1024*1024:
                            buffer = b'' 
            except (OSError, ValueError):
                LOG.exception('Cannot start rpicam-vid')
            finally:
                if process is not None:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
                    process.stdout.close()
                self.publish(None)
            if not self.stop.is_set() and not self.reload.is_set() and self.has_clients():
                LOG.warning('Camera stream interrupted; retrying in 3 seconds')
                self.stop.wait(3)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def send(self, body, content_type, code=200, headers=None):
        self.send_response(code)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        for key, value in (headers or {}).items():
            self.send_header(key, str(value))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.path != '/api/config' or self.headers.get('X-Camera-Control') != '1':
            self.send_error(403)
            return
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 2048:
                raise ValueError('Invalid request size')
            data = json.loads(self.rfile.read(length))
            settings = validate_update(mode(), data)
            self.server.broadcast.reconfigure(settings)
            self.send(json.dumps(settings).encode(), 'application/json')
        except (ValueError, TypeError) as error:
            self.send(str(error).encode(), 'text/plain', 400)
        except OSError:
            self.send(b'Cannot persist capture settings', 'text/plain', 503)

    def do_GET(self):
        if self.path == '/api/config':
            self.send(json.dumps(mode()).encode(), 'application/json')
            return
        if self.path == '/snapshot.jpg':
            if mode()['capture_mode'] != 'snapshots':
                self.send_error(409)
                return
            frame = self.server.broadcast.snapshot(self.headers.get('X-Camera-After'))
            if frame is None:
                self.send_error(503)
                return
            jpeg, captured_at, token = frame
            self.send(jpeg, 'image/jpeg', headers={'X-Camera-Captured-At': captured_at,
                      'X-Camera-Sequence': token})
            return
        if self.path != '/stream.ts':
            self.send_error(404)
            return
        client = self.server.broadcast.subscribe()
        if client is None:
            self.send_error(503)
            return
        self.connection.settimeout(10)
        try:
            self.send_response(200)
            self.send_header('Content-Type', 'video/mp2t')
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            while not self.server.broadcast.stop.is_set():
                try:
                    chunk = client.get(timeout=10)
                except queue.Empty:
                    break
                if chunk is None:
                    break
                self.wfile.write(chunk)
        except (OSError, TimeoutError):
            pass
        finally:
            self.server.broadcast.remove(client)


def main():
    logging.basicConfig(level=logging.INFO)
    broadcast = Broadcast()
    server = ThreadingHTTPServer(('127.0.0.1', 8084), Handler)
    server.broadcast = broadcast
    worker = threading.Thread(target=broadcast.run, daemon=True)
    worker.start()

    def shutdown(*_):
        broadcast.stop.set()
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    try:
        server.serve_forever()
    finally:
        broadcast.stop.set()
        server.server_close()


if __name__ == '__main__':
    main()
