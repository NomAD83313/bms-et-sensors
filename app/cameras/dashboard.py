"""Local camera dashboard. Credentials and RTSP remain on the Pi."""
import json
import logging
import os
from pathlib import Path
import shutil
import subprocess
import threading
import time
import queue
import io
from collections import OrderedDict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlsplit, parse_qs

try:
    from .source import input_args
except ImportError:
    from source import input_args


try:
    from . import control, anomaly_control, history, source
    from .preview_quality import DecodeGuard, decoder_error_kind
    from .roi import validate_roi, roi_input_dimensions
except ImportError:
    import control
    import anomaly_control
    import history
    import source
    from preview_quality import DecodeGuard, decoder_error_kind
    from roi import validate_roi, roi_input_dimensions

ROOT = Path(__file__).resolve().parent
RECORDINGS = Path(os.environ.get('RECORDING_DIR', ROOT / 'runtime/recordings')).resolve()
WEB = ROOT / 'web'
LOG = logging.getLogger('camera-preview')
try:
    from .model_profiles import ANALYSIS_SOURCE
except ImportError:
    from model_profiles import ANALYSIS_SOURCE


def browser_preview(jpeg):
    """Fit native snapshots into the browser canvas without cropping the scene."""
    from PIL import Image
    image = Image.open(io.BytesIO(jpeg))
    image.draft('RGB', (960, 540))
    image = image.convert('RGB')
    image.thumbnail((960, 540), Image.Resampling.BICUBIC)
    canvas = Image.new('RGB', (960, 540))
    canvas.paste(image, ((960-image.width)//2, (540-image.height)//2))
    result = io.BytesIO()
    canvas.save(result, format='JPEG', quality=85)
    return result.getvalue()


def clip_path(name):
    if Path(name).name != name or not name.endswith('.mkv'):
        raise ValueError('Invalid clip')
    path = RECORDINGS / name
    if path.is_symlink() or not path.is_file():
        raise ValueError('Clip not found')
    return path


def status():
    clips = []
    for p in RECORDINGS.glob('*.mkv'):
        try:
            if p.is_symlink():
                continue
            stat = p.stat()
            clips.append(dict(name=p.name, bytes=stat.st_size, timestamp=stat.st_mtime,
                              kind='event' if p.name.startswith('event-') else 'unclassified' if p.name.startswith('unclassified-') else 'continuous'))
        except FileNotFoundError:
            continue
    clips.sort(key=lambda p: p['timestamp'], reverse=True)
    newest = clips[0]['timestamp'] if clips else 0
    state = control.recording_state()
    mode = os.environ.get('CAMERA_RECORDING_MODE', 'continuous')
    details = control.recording_details()
    analysis = anomaly_control.read_json(anomaly_control.DATA / 'status.json', {})
    analysis_state = analysis.get('state', 'offline') if 0 <= time.time() - analysis.get('updated', 0) < 20 else 'offline'
    healthy = control.enabled() and state == 'recording' and time.time() - newest < int(os.environ.get('CLIP_SECONDS', '60')) + 60
    if mode == 'anomaly':
        healthy = control.enabled() and state in ('buffering', 'saving_event')
    resolution = os.environ.get('CAMERA_RESOLUTION', 'Main stream')
    if source.capture_url():
        capture = source.capture_settings()
        resolution = f"{capture['width']} × {capture['height']}"
    return dict(camera_name=os.environ.get('CAMERA_NAME', 'Camera 01'),
                camera_description=os.environ.get('CAMERA_DESCRIPTION', 'Network camera'),
                camera_resolution=resolution, service=state, healthy=healthy, recording_enabled=control.enabled(), recording_mode=mode,
                snapshot_export_failed=details.get('export_failed', False),
                capture_mode=details.get('capture_mode', 'video'),
                event_active=details.get('event_active', False), event_until=details.get('event_until'),
                analysis_available=details.get('analysis_available', False),
                analysis_state=analysis_state, monitoring_enabled=analysis.get('monitoring_enabled', False),
                baseline_ready=analysis.get('baseline_ready', False),
                analysis_score=analysis.get('score'), analysis_threshold=analysis.get('threshold'),
                preview_quality=LIVE.quality(),
                pre_seconds=float(os.environ.get('CAMERA_EVENT_PRE_SEC', '7')),
                post_seconds=float(os.environ.get('CAMERA_EVENT_POST_SEC', '10')),
                clips=clips, total_bytes=sum(p['bytes'] for p in clips), free_bytes=shutil.disk_usage(RECORDINGS).free,
                clip_seconds=int(os.environ.get('CLIP_SECONDS', '60')), max_storage_bytes=int(os.environ.get('MAX_STORAGE_MB', '10240'))*1024*1024,
                max_age_hours=float(os.environ.get('MAX_AGE_HOURS', '24')), live_available=bool(os.environ.get('CAMERA_URL')))


class LivePreview:
    def __init__(self):
        self.condition = threading.Condition()
        self.frame = None
        self.updated = 0
        self.worker = None
        self.last_request = 0
        self.guard = DecodeGuard()
        self.frame_token = None
        self.last_error_log = float('-inf')
        self.original_frame = None
        self.originals = OrderedDict()
        self.sequence = 0
        self.preview_source = self.preview_frame = None

    def get(self, max_age=5, wait_seconds=20, with_token=False, with_frame_id=False, full_resolution=False):
        with self.condition:
            self.last_request = time.monotonic()
            if self.worker is None or not self.worker.is_alive():
                self.frame, self.updated, self.frame_token = None, 0, None
                self.original_frame = None
                self.originals.clear()
                self.preview_source = self.preview_frame = None
                self.guard.pending = None
                self.worker = threading.Thread(target=self.run, daemon=True)
                self.worker.start()
            deadline = time.monotonic()+wait_seconds
            while self.frame is None or time.monotonic()-self.updated > max_age:
                if with_token and self.guard.recovering(time.monotonic()):
                    return (None, None, None) if with_frame_id else (None, None)
                remaining = deadline-time.monotonic()
                if remaining <= 0:
                    return (None, None, None) if with_frame_id else (None, None) if with_token else None
                self.condition.wait(remaining)
            frame = self.original_frame if full_resolution else self.frame
            if frame is None:
                return (None, None, None) if with_frame_id else (None, None) if with_token else None
            if not full_resolution and self.frame is self.original_frame:
                if self.preview_source is not self.frame:
                    self.preview_frame = browser_preview(self.frame)
                    self.preview_source = self.frame
                frame = self.preview_frame
            if with_frame_id:
                self.sequence += 1
                frame_id = self.frame_token + '-' + str(self.sequence)
                now = time.monotonic()
                self.originals[frame_id] = (now, self.original_frame or self.frame, self.frame_token)
                total = sum(len(item[1]) for item in self.originals.values())
                while self.originals and (len(self.originals) > 32 or total > 64*1024*1024
                                          or now - next(iter(self.originals.values()))[0] > 15):
                    _, expired = self.originals.popitem(last=False)
                    total -= len(expired[1])
                return frame, self.frame_token, frame_id
            return (frame, self.frame_token) if with_token else frame

    def original(self, frame_id):
        with self.condition:
            item = self.originals.get(frame_id)
            if (item is None or time.monotonic()-item[0] > 15
                    or not self.validate(item[2])):
                return None
            return item[1]

    def quality(self):
        with self.condition:
            now = time.monotonic()
            running = self.worker is not None and self.worker.is_alive()
            state = 'ok' if self.frame is not None and now - self.updated < 1 else 'waiting'
            if self.guard.recovering(now):
                state = 'recovering'
            elif not running:
                state = 'unavailable' if self.last_request and now - self.last_request < 30 else 'idle'
            return dict(state=state, token=self.guard.token, decoder_errors=self.guard.errors,
                        discarded_frames=self.guard.discarded, last_error=self.guard.last_error,
                        last_error_at=self.guard.last_error_at)

    def geometry(self):
        """Read source dimensions from JPEG headers without decoding pixels."""
        with self.condition:
            frame = self.original_frame
        if frame is None:
            return None
        try:
            from PIL import Image
            with Image.open(io.BytesIO(frame)) as image:
                return image.size
        except (OSError, ValueError):
            return None

    def validate(self, token):
        with self.condition:
            quality = self.quality()
            return quality['state'] == 'ok' and token == quality['token']

    def decoder_error(self, kind):
        with self.condition:
            now = time.monotonic()
            self.guard.note_error(kind, now, time.time())
            self.frame, self.updated, self.frame_token = None, 0, None
            self.original_frame = None
            self.originals.clear()
            self.preview_source = self.preview_frame = None
            self.condition.notify_all()
            if now - self.last_error_log >= 10:
                # Only static categories and counters are safe to log; never the source line.
                LOG.warning('Preview stream issue: %s; errors=%d; analysis frames excluded', kind, self.guard.errors)
                self.last_error_log = now

    def read_errors(self, stream):
        while True:
            # Bound each read even if a remote endpoint emits a very long diagnostic.
            line = stream.readline(4096)
            if not line:
                return
            kind = decoder_error_kind(line.decode('utf-8', errors='replace'))
            if kind:
                self.decoder_error(kind)

    def run_snapshots(self):
        signature = source.capture_signature()
        last_token = None
        while time.monotonic()-self.last_request < 30 and source.capture_signature() == signature:
            try:
                jpeg, captured_at, token = source.snapshot(last_token)
                if token != last_token:
                    with self.condition:
                        if not self.guard.recovering(time.monotonic()):
                            self.frame, self.updated, self.frame_token = jpeg, time.monotonic(), self.guard.token
                            self.original_frame = jpeg
                            self.condition.notify_all()
                    last_token = token
            except (OSError, ValueError):
                self.decoder_error('stream_error')
            time.sleep(.05)

    def run(self):
        if source.capture_settings()['capture_mode'] == 'snapshots':
            return self.run_snapshots()
        signature = source.capture_signature()
        url = os.environ.get('CAMERA_URL')
        if not url:
            return
        full_read, full_write = os.pipe()
        full_stream = os.fdopen(full_read, 'rb')
        originals = queue.Queue(maxsize=2)
        stopped = threading.Event()
        args = ['ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'warning',
                '-fflags', '+discardcorrupt', '-err_detect', 'explode',
                *input_args(url),
                '-filter_complex_threads', '1', '-filter_complex',
                '[0:v]fps=10,split=2[full][small];'
                '[small]scale=960:540:force_original_aspect_ratio=decrease:force_divisible_by=2,'
                'pad=960:540:(ow-iw)/2:(oh-ih)/2[preview]',
                '-map', '[preview]', '-an', '-c:v', 'mjpeg', '-q:v', '5', '-threads', '1', '-f', 'image2pipe', 'pipe:1',
                '-map', '[full]', '-an', '-c:v', 'mjpeg', '-q:v', '3', '-threads', '1', '-f', 'image2pipe', f'pipe:{full_write}']

        def read_originals():
            try:
                for frame in jpeg_frames(full_stream):
                    while not stopped.is_set():
                        try:
                            originals.put(frame, timeout=.2)
                            break
                        except queue.Full:
                            pass
                    if stopped.is_set():
                        return
            except OSError:
                # The preview reader rejects a missing half-pair below.
                pass
            finally:
                while not stopped.is_set():
                    try:
                        originals.put(None, timeout=.2)
                        return
                    except queue.Full:
                        pass

        try:
            process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, pass_fds=(full_write,))
            os.close(full_write)
            full_write = None
            errors = threading.Thread(target=self.read_errors, args=(process.stderr,), daemon=True)
            errors.start()
            full_reader = threading.Thread(target=read_originals, daemon=True)
            full_reader.start()
            try:
                for frame in jpeg_frames(process.stdout):
                    if time.monotonic()-self.last_request >= 30 or source.capture_signature() != signature:
                        break
                    try:
                        original = originals.get(timeout=5)
                    except queue.Empty:
                        original = None
                    if original is None:
                        self.decoder_error('stream_error')
                        break
                    with self.condition:
                        # Admit a synchronized pair only after decoder diagnostics settle.
                        accepted = self.guard.feed((frame, original), time.monotonic())
                        if accepted is not None:
                            pair, self.updated, self.frame_token = accepted
                            self.frame, self.original_frame = pair
                            self.condition.notify_all()
                else:
                    self.decoder_error('stream_error')
            finally:
                stopped.set()
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                full_reader.join(timeout=2)
                process.stdout.close()
                errors.join(timeout=2)
                process.stderr.close()
        except OSError:
            self.decoder_error('stream_error')
        finally:
            if full_write is not None:
                os.close(full_write)
            full_stream.close()


def jpeg_frames(stream):
    """Read complete ordered JPEGs; never resynchronize one half of a frame pair."""
    buffer = b''
    while True:
        chunk = stream.read1(65536)
        if not chunk:
            if buffer:
                raise OSError('Incomplete JPEG output')
            return
        buffer += chunk
        while True:
            end = buffer.find(b'\xff\xd9')
            if end < 0:
                break
            frame, buffer = buffer[:end+2], buffer[end+2:]
            if not frame.startswith(b'\xff\xd8') or len(frame) > 16*1024*1024:
                raise OSError('Invalid JPEG output')
            yield frame
        if len(buffer) > 16*1024*1024:
            raise OSError('JPEG output limit exceeded')


LIVE = LivePreview()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def send(self, data, content_type, code=200, headers=None):
        self.send_response(code)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        path = urlsplit(self.path).path
        if path not in ('/api/recording/start', '/api/recording/stop', '/api/capture', '/api/anomaly/learn', '/api/anomaly/monitor', '/api/anomaly/pause', '/api/anomaly/configure', '/api/anomaly/resize', '/api/anomaly/confirmation', '/api/anomaly/model', '/api/anomaly/roi', '/api/delete/event', '/api/delete/recording', '/api/delete/all-events', '/api/delete/all-recordings', '/api/delete/history'):
            return self.send(b'Not found', 'text/plain', 404)
        # Require a custom header to prevent cross-origin form submissions.
        if self.headers.get('X-Camera-Control') != '1':
            return self.send(b'Forbidden', 'text/plain', 403)
        try:
            if path == '/api/capture':
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 2048:
                    raise ValueError('Invalid capture request')
                body = json.loads(self.rfile.read(length))
                try:
                    from .capture_settings import validate_update
                except ImportError:
                    from capture_settings import validate_update
                validate_update(source.capture_settings(), body)
                settings = source.configure_capture(body)
                session = anomaly_control.read_json(anomaly_control.DATA / 'session.json', {})
                anomaly_control.request('pause', session.get('multiplier', 1.25))
                if os.environ.get('CAMERA_RECORDING_MODE', 'continuous') == 'anomaly':
                    control.set_enabled(False)
                return self.send(json.dumps(dict(settings=settings, detection_paused=True)).encode(), 'application/json')
            if path.startswith('/api/delete/'):
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 <= length <= 1024:
                    raise ValueError('Invalid request size')
                body = json.loads(self.rfile.read(length) or b'{}')
                result = history.delete_saved(RECORDINGS, anomaly_control.DATA,
                                              path.rsplit('/', 1)[-1], body.get('name'))
                return self.send(json.dumps(result).encode(), 'application/json')
            if path.startswith('/api/anomaly/'):
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 <= length <= 1024:
                    raise ValueError('Invalid request size')
                body = json.loads(self.rfile.read(length) or b'{}')
                action = path.rsplit('/', 1)[-1]
                if action in ('roi', 'resize'):
                    if action == 'roi' and 'roi' not in body:
                        raise ValueError('Select an area or explicitly request the full frame')
                    session = anomaly_control.read_json(anomaly_control.DATA / 'session.json', {})
                    roi = validate_roi(body.get('roi') if action == 'roi' else session.get('roi'))
                    if roi is not None:
                        geometry = LIVE.geometry()
                        if geometry is None:
                            raise ValueError('A source frame is required to select an area')
                        size = body.get('input_size') if action == 'resize' else session.get('input_size', anomaly_control.DEFAULT_INPUT_SIZE)
                        roi_input_dimensions(size,
                                             *geometry, roi)
                command = anomaly_control.request(action, body.get('multiplier', 1.25),
                                                  input_size=body.get('input_size'),
                                                  confirmation_frames=body.get('confirmation_frames'),
                                                  model_key=body.get('model_key'), roi=body.get('roi'))
                if os.environ.get('CAMERA_RECORDING_MODE', 'continuous') == 'anomaly' and action in ('pause', 'learn', 'resize', 'model', 'roi'):
                    # Stopping the buffer must also work while the worker is offline.
                    control.set_enabled(False)
                return self.send(json.dumps(command).encode(), 'application/json', 202)
            if os.environ.get('CAMERA_RECORDING_MODE', 'continuous') == 'anomaly':
                return self.send(b'Use anomaly detection controls', 'text/plain', 409)
            control.set_enabled(path.endswith('/start'))
            self.send(json.dumps(status()).encode(), 'application/json')
        except (ValueError, TypeError, AttributeError):
            self.send(b'Invalid control request', 'text/plain', 400)
        except OSError:
            self.send(b'Recording control unavailable', 'text/plain', 503)

    def do_GET(self):
        try:
            self.route()
        except (BrokenPipeError, ConnectionResetError):
            pass
        except (ValueError, FileNotFoundError):
            self.send(b'Not found', 'text/plain', 404)
        except (OSError, subprocess.SubprocessError):
            self.send(b'Camera service unavailable', 'text/plain', 503)

    def route(self):
        path = unquote(urlsplit(self.path).path)
        if path == '/api/capture':
            return self.send(json.dumps(dict(available=bool(source.capture_url()), settings=source.capture_settings())).encode(), 'application/json')
        if path == '/api/anomaly':
            result = anomaly_control.status()
            videos = []
            for metadata in RECORDINGS.glob('event-*.json'):
                video = anomaly_control.read_json(metadata, {})
                if metadata.with_suffix('.mkv').is_file():
                    videos.append((metadata.with_suffix('.mkv').name, video))
            for event in result.get('events', []):
                event['videos'] = [name for name, video in videos if video.get('start', 0) <= event['timestamp'] <= video.get('end', 0)]
            details = control.recording_details()
            result['video_pending'] = details.get('event_active', False)
            result['event_until'] = details.get('event_until')
            result['recording_mode'] = os.environ.get('CAMERA_RECORDING_MODE', 'continuous')
            result['recording_enabled'] = control.enabled()
            result['preview_quality'] = LIVE.quality()
            geometry = LIVE.geometry()
            if geometry is not None:
                result.update(source_width=geometry[0], source_height=geometry[1])
                try:
                    width, height = roi_input_dimensions(result.get('input_size', anomaly_control.DEFAULT_INPUT_SIZE),
                                                        *geometry, result['roi'])
                    result.update(effective_input_width=width, effective_input_height=height,
                                  roi_geometry_valid=True)
                    model_key = anomaly_control.validate_model(result['model_key'])
                    heat_width, heat_height = anomaly_control.heatmap_dimensions(width, height, model_key)
                    result.update(heatmap_width=heat_width, heatmap_height=heat_height)
                except ValueError:
                    # Keep area reset available after a source aspect-ratio change.
                    result.update(effective_input_width=None, effective_input_height=None,
                                  heatmap_width=None, heatmap_height=None, roi_geometry_valid=False)
            return self.send(json.dumps(result).encode(), 'application/json')
        if path.startswith('/api/anomaly/events/'):
            return self.send(anomaly_control.event_path(path.removeprefix('/api/anomaly/events/')).read_bytes(), 'image/jpeg')
        if path == '/api/status':
            return self.send(json.dumps(status()).encode(), 'application/json')
        if path == '/api/live.jpg':
            query = parse_qs(urlsplit(self.path).query)
            frame_id = None
            if 'original' in query:
                frame = LIVE.original(query['original'][0])
                return self.send(frame or b'Original frame expired or invalidated',
                                 'image/jpeg' if frame else 'text/plain', 200 if frame else 503,
                                 None if frame else {'X-Camera-Stream-Error': '1'})
            if 'check' in query:
                valid = LIVE.validate(query['check'][0])
                return self.send(json.dumps(dict(valid=valid)).encode(), 'application/json', 200 if valid else 503,
                                 {'X-Camera-Stream-Error': '1'} if not valid else None)
            if 'fresh' in query or query.get('analysis') == ['1']:
                frame, token, frame_id = LIVE.get(max_age=.2, wait_seconds=4, with_token=True, with_frame_id=True,
                                                full_resolution=query.get('analysis') == ['1'])
            else:
                frame, token = LIVE.get(), None
            headers = {'X-Camera-Frame-Token': token} if frame and token else None
            if headers and frame_id:
                headers['X-Camera-Frame-ID'] = frame_id
                if query.get('analysis') == ['1']:
                    headers['X-Camera-Analysis-Source'] = ANALYSIS_SOURCE
            if not frame and LIVE.quality()['state'] == 'recovering':
                return self.send(b'Video stream error; damaged frames excluded', 'text/plain', 503,
                                 {'X-Camera-Stream-Error': '1'})
            return self.send(frame or b'Live preview unavailable', 'image/jpeg' if frame else 'text/plain', 200 if frame else 503, headers)
        if path.startswith('/api/download/'):
            clip = clip_path(path.removeprefix('/api/download/'))
            with clip.open('rb') as clip_file:
                self.send_response(200)
                self.send_header('Content-Type', 'video/x-matroska')
                self.send_header('Content-Disposition', 'attachment; filename="'+clip.name+'"')
                self.send_header('Content-Length', str(clip.stat().st_size))
                self.end_headers()
                shutil.copyfileobj(clip_file, self.wfile)
            return
        if path.startswith('/api/play/'):
            clip = clip_path(path.removeprefix('/api/play/'))
            # Fragmented MP4 allows playback without a second copy of each clip.
            process = subprocess.Popen(['ffmpeg', '-nostdin', '-loglevel', 'quiet', '-i', str(clip), '-map', '0:v:0', '-an', '-c:v', 'copy',
                                        '-movflags', 'frag_keyframe+empty_moov+default_base_moof', '-f', 'mp4', 'pipe:1'], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            try:
                self.send_response(200)
                self.send_header('Content-Type', 'video/mp4')
                self.send_header('Cache-Control', 'no-store')
                self.end_headers()
                shutil.copyfileobj(process.stdout, self.wfile)
            finally:
                process.stdout.close()
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            return
        assets = {'/': ('index.html', 'text/html; charset=utf-8'), '/app.js': ('app.js', 'text/javascript'), '/style.css': ('style.css', 'text/css')}
        if path in assets:
            filename, content_type = assets[path]
            return self.send((WEB / filename).read_bytes(), content_type)
        self.send(b'Not found', 'text/plain', 404)


if __name__ == '__main__':
    RECORDINGS.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer((os.environ.get('DASHBOARD_HOST', '0.0.0.0'), int(os.environ.get('DASHBOARD_PORT', '8082'))), Handler)
    print('Camera dashboard listening on port', server.server_port, flush=True)
    server.serve_forever()
