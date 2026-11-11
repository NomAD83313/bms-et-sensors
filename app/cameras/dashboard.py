"""Local camera dashboard. Credentials and RTSP remain on the Pi."""
import json
import logging
import os
from pathlib import Path
import shutil
import subprocess
import threading
import time
import io
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlsplit, parse_qs

try:
    from .source import preview_input_args
except ImportError:
    from source import preview_input_args


try:
    from . import control, anomaly_control, history, source
    from .preview_quality import DecodeGuard, decoder_error_kind
    from .roi import validate_roi, roi_input_dimensions, roi_bounds
    from .learning_sessions import LearningSessions, MAX_SESSIONS
except ImportError:
    import control
    import anomaly_control
    import history
    import source
    from preview_quality import DecodeGuard, decoder_error_kind
    from roi import validate_roi, roi_input_dimensions, roi_bounds
    from learning_sessions import LearningSessions, MAX_SESSIONS

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
                              kind='event' if p.name.startswith('event-') else 'continuous'))
        except FileNotFoundError:
            continue
    clips.sort(key=lambda p: p['timestamp'], reverse=True)
    newest = clips[0]['timestamp'] if clips else 0
    state = control.recording_state()
    mode = os.environ.get('CAMERA_RECORDING_MODE', 'continuous')
    details = control.recording_details()
    analysis = anomaly_control.read_json(anomaly_control.DATA / 'status.json', {})
    analysis_state = analysis.get('state', 'offline') if anomaly_control.state_is_current(analysis) else 'offline'
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
                preview_quality=browser_live().quality(),
                pre_seconds=float(os.environ.get('CAMERA_EVENT_PRE_SEC', '7')),
                post_seconds=float(os.environ.get('CAMERA_EVENT_POST_SEC', '10')),
                clips=clips, total_bytes=sum(p['bytes'] for p in clips), free_bytes=shutil.disk_usage(RECORDINGS).free,
                clip_seconds=int(os.environ.get('CLIP_SECONDS', '60')), max_storage_bytes=int(os.environ.get('MAX_STORAGE_MB', '10240'))*1024*1024,
                max_age_hours=float(os.environ.get('MAX_AGE_HOURS', '24')), live_available=bool(os.environ.get('CAMERA_URL')))


class LivePreview:
    def __init__(self, browser=False):
        self.browser = browser
        self.condition = threading.Condition()
        self.frame = None
        self.updated = 0
        self.worker = None
        self.last_request = 0
        self.guard = DecodeGuard(cooldown=.25 if browser else 1.0)
        self.frame_token = None
        self.last_error_log = float('-inf')
        self.original_frame = None
        self.preview_source = self.preview_frame = None

    def get(self, max_age=5, wait_seconds=20, with_token=False, full_resolution=False):
        with self.condition:
            self.last_request = time.monotonic()
            deadline = time.monotonic()+wait_seconds
            while True:
                if self.worker is None or not self.worker.is_alive():
                    self.frame, self.updated, self.frame_token = None, 0, None
                    self.original_frame = None
                    self.preview_source = self.preview_frame = None
                    self.guard.pending = None
                    self.worker = threading.Thread(target=self.run, daemon=True)
                    self.worker.start()
                if self.frame is not None and time.monotonic()-self.updated <= max_age:
                    break
                if with_token and self.guard.recovering(time.monotonic()):
                    return None, None
                remaining = deadline-time.monotonic()
                if remaining <= 0:
                    return (None, None) if with_token else None
                self.condition.wait(min(remaining, .25))
            frame = self.original_frame if full_resolution else self.frame
            if frame is None:
                return (None, None) if with_token else None
            if not full_resolution and self.frame is self.original_frame:
                if self.preview_source is not self.frame:
                    self.preview_frame = browser_preview(self.frame)
                    self.preview_source = self.frame
                frame = self.preview_frame
            return (frame, self.frame_token) if with_token else frame

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
                jpeg, _, token = source.snapshot(last_token)
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
        main_url = os.environ.get('CAMERA_URL')
        if not main_url:
            return
        url = source.browser_stream_url(main_url) if self.browser else main_url
        browser_substream = self.browser and url != main_url
        separate_analysis_stream = source.browser_stream_url(main_url) != main_url
        frame_filter = ('fps=10,scale=960:540:force_original_aspect_ratio=decrease:'
                        'force_divisible_by=2,pad=960:540:(ow-iw)/2:(oh-ih)/2'
                        if browser_substream else 'fps=5' if separate_analysis_stream else 'fps=10')
        args = ['ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'warning',
                *preview_input_args(url, transport='tcp', strict=not browser_substream),
                '-map', '0:v:0', '-an', '-vf', frame_filter,
                '-c:v', 'mjpeg', '-q:v', '5' if browser_substream else '3', '-threads', '1',
                '-f', 'image2pipe', 'pipe:1']

        try:
            process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            watchdog_stop = threading.Event()
            progress = [time.monotonic()]
            first_frame = [False]
            def watchdog():
                while not watchdog_stop.wait(.25):
                    # RTSP negotiation can take longer than a normal frame gap.
                    # Once frames arrive, stop a stalled decoder before its queue
                    # can turn into a multi-second browser delay.
                    timeout = 2 if first_frame[0] else 5
                    if time.monotonic() - progress[0] > timeout:
                        process.kill()
                        return
            errors = threading.Thread(target=self.read_errors, args=(process.stderr,), daemon=True)
            errors.start()
            watchdog_thread = threading.Thread(target=watchdog, daemon=True)
            watchdog_thread.start()
            try:
                for frame in jpeg_frames(process.stdout):
                    progress[0] = time.monotonic()
                    first_frame[0] = True
                    if time.monotonic()-self.last_request >= 30 or source.capture_signature() != signature:
                        break
                    with self.condition:
                        accepted = self.guard.feed(frame, time.monotonic())
                        if accepted is not None:
                            self.frame, self.updated, self.frame_token = accepted
                            self.original_frame = None if browser_substream else self.frame
                            self.condition.notify_all()
                else:
                    self.decoder_error('stream_error')
            finally:
                watchdog_stop.set()
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                process.stdout.close()
                watchdog_thread.join(timeout=1)
                errors.join(timeout=2)
                process.stderr.close()
        except OSError:
            self.decoder_error('stream_error')


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
BROWSER = LivePreview(browser=True)
POST_PATHS = frozenset((
    '/api/capture',
    '/api/anomaly/learn', '/api/anomaly/monitor', '/api/anomaly/pause',
    '/api/anomaly/configure', '/api/anomaly/resize',
    '/api/anomaly/confirmation', '/api/anomaly/model', '/api/anomaly/roi', '/api/anomaly/color',
    '/api/anomaly/add-session', '/api/anomaly/replace-session',
    '/api/anomaly/remove-session', '/api/anomaly/rename-session', '/api/anomaly/rebuild-sessions',
    '/api/delete/event', '/api/delete/recording', '/api/delete/all-events',
    '/api/delete/all-recordings', '/api/delete/history',
))
ASSETS = {
    '/': ('index.html', 'text/html; charset=utf-8'),
    '/app.js': ('app.js', 'text/javascript'),
    '/style.css': ('style.css', 'text/css'),
}


def browser_live():
    """Keep browser traffic independent when the camera offers a substream."""
    url = os.environ.get('CAMERA_URL', '')
    return BROWSER if url and source.browser_stream_url(url) != url else LIVE


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

    def read_json_body(self, maximum, required=False):
        length = int(self.headers.get('Content-Length', '0'))
        minimum = 1 if required else 0
        if not minimum <= length <= maximum:
            raise ValueError('Invalid request size')
        return json.loads(self.rfile.read(length) or b'{}')

    def update_capture(self):
        body = self.read_json_body(2048, required=True)
        try:
            from .capture_settings import validate_update
        except ImportError:
            from capture_settings import validate_update
        previous = source.capture_settings()
        validate_update(previous, body)
        settings = source.configure_capture(body)
        session = anomaly_control.read_json(anomaly_control.DATA / 'session.json', {})
        geometry_changed = any(previous.get(key) != settings.get(key) for key in ('width', 'height'))
        anomaly_control.request('invalidate' if geometry_changed else 'pause', session.get('multiplier', 1.25))
        if os.environ.get('CAMERA_RECORDING_MODE', 'continuous') == 'anomaly':
            control.set_enabled(False)
        return self.send(json.dumps(dict(settings=settings, detection_paused=True)).encode(),
                         'application/json')

    def delete_saved(self, path):
        body = self.read_json_body(1024)
        result = history.delete_saved(RECORDINGS, anomaly_control.DATA,
                                      path.rsplit('/', 1)[-1], body.get('name'))
        return self.send(json.dumps(result).encode(), 'application/json')

    def control_anomaly(self, path):
        body = self.read_json_body(4096)
        action = path.rsplit('/', 1)[-1]
        session_actions = ('learn', 'add-session', 'replace-session', 'remove-session',
                           'rename-session', 'rebuild-sessions')
        if action in session_actions:
            state = anomaly_control.status()
            if state.get('state') in ('learning', 'training'):
                return self.send(b'Pause the current learning operation first', 'text/plain', 409)
            store = LearningSessions(anomaly_control.DATA)
            if action not in ('learn', 'rebuild-sessions'):
                store.check(action.removesuffix('-session'), body.get('session_id'))
            if action in ('add-session', 'replace-session', 'rebuild-sessions'):
                if not self.session_configuration_matches(state, store.catalog()):
                    return self.send(b'Create a new baseline for the current settings first', 'text/plain', 409)
        if action in ('roi', 'resize'):
            if action == 'roi' and 'roi' not in body:
                raise ValueError('Select an area or explicitly request the full frame')
            session = anomaly_control.read_json(anomaly_control.DATA / 'session.json', {})
            roi = validate_roi(body.get('roi') if action == 'roi' else session.get('roi'))
            if roi is not None:
                geometry = LIVE.geometry()
                if geometry is None:
                    raise ValueError('A source frame is required to select an area')
                size = (body.get('input_size') if action == 'resize'
                        else session.get('input_size', anomaly_control.DEFAULT_INPUT_SIZE))
                roi_input_dimensions(size, *geometry, roi)
        command = anomaly_control.request(
            action, body.get('multiplier', 1.25), input_size=body.get('input_size'),
            confirmation_frames=body.get('confirmation_frames'),
            model_key=body.get('model_key'), roi=body.get('roi'),
            learning_seconds=body.get('learning_seconds'), session_id=body.get('session_id'),
            session_name=body.get('session_name', ''), session_note=body.get('session_note', ''),
            color_mode=body.get('color_mode'))
        if (os.environ.get('CAMERA_RECORDING_MODE', 'continuous') == 'anomaly'
                and action in ('pause', 'learn', 'resize', 'model', 'roi', 'color', 'add-session',
                               'replace-session', 'remove-session', 'rebuild-sessions')):
            # Stopping the buffer must also work while the worker is offline.
            control.set_enabled(False)
        return self.send(json.dumps(command).encode(), 'application/json', 202)

    def do_POST(self):
        path = urlsplit(self.path).path
        if path not in POST_PATHS:
            return self.send(b'Not found', 'text/plain', 404)
        # Require a custom header to prevent cross-origin form submissions.
        if self.headers.get('X-Camera-Control') != '1':
            return self.send(b'Forbidden', 'text/plain', 403)
        try:
            if path == '/api/capture':
                return self.update_capture()
            if path.startswith('/api/delete/'):
                return self.delete_saved(path)
            return self.control_anomaly(path)
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

    def anomaly_status(self):
        result = anomaly_control.status()
        store = LearningSessions(anomaly_control.DATA)
        result.update(learning_sessions=store.public(), session_limit=MAX_SESSIONS,
                      sessions_compatible=self.session_configuration_matches(result, store.catalog()))
        videos = []
        for metadata in RECORDINGS.glob('event-*.json'):
            video = anomaly_control.read_json(metadata, {})
            if metadata.with_suffix('.mkv').is_file():
                videos.append((metadata.with_suffix('.mkv').name, video))
        for event in result.get('events', []):
            event['videos'] = [name for name, video in videos
                               if video.get('start', 0) <= event['timestamp'] <= video.get('end', 0)]
        details = control.recording_details()
        result.update(video_pending=details.get('event_active', False),
                      event_until=details.get('event_until'),
                      recording_mode=os.environ.get('CAMERA_RECORDING_MODE', 'continuous'),
                      recording_enabled=control.enabled(), preview_quality=LIVE.quality())
        geometry = LIVE.geometry()
        if geometry is not None:
            result.update(source_width=geometry[0], source_height=geometry[1])
            try:
                width, height = roi_input_dimensions(
                    result.get('input_size', anomaly_control.DEFAULT_INPUT_SIZE),
                    *geometry, result.get('roi'))
                model_key = anomaly_control.validate_model(
                    result.get('model_key', anomaly_control.DEFAULT_MODEL))
                heat_width, heat_height = anomaly_control.heatmap_dimensions(
                    width, height, model_key)
                result.update(effective_input_width=width, effective_input_height=height,
                              heatmap_width=heat_width, heatmap_height=heat_height,
                              roi_geometry_valid=True)
            except ValueError:
                # Keep area reset available after a source aspect-ratio change.
                result.update(effective_input_width=None, effective_input_height=None,
                              heatmap_width=None, heatmap_height=None,
                              roi_geometry_valid=False)
        return self.send(json.dumps(result).encode(), 'application/json')

    @staticmethod
    def session_configuration_matches(state, catalog):
        config = catalog.get('configuration')
        if not catalog['sessions'] or not config:
            return False
        geometry = LIVE.geometry()
        if geometry is None or list(geometry) != config['source_size']:
            return False
        return (state.get('model_key') == config['model_key']
                and state.get('input_size') == config['input_size']
                and state.get('roi') == config['roi']
                and state.get('color_mode', 'rgb') == config['color_mode'])

    def learning_thumbnail(self, identifier):
        from PIL import Image, ImageDraw
        store = LearningSessions(anomaly_control.DATA)
        path = store.thumbnail(identifier)
        roi = validate_roi(store.catalog()['configuration']['roi'])
        with Image.open(path) as original:
            original.draft('RGB', (320, 240))
            image = original.convert('RGB')
        image.thumbnail((320, 240))
        left, top, right, bottom = roi_bounds(image.width, image.height, roi)
        if roi is not None:
            shade = Image.new('RGBA', image.size, (0, 0, 0, 100))
            shade.paste((0, 0, 0, 0), (left, top, right, bottom))
            image = Image.alpha_composite(image.convert('RGBA'), shade).convert('RGB')
        draw = ImageDraw.Draw(image)
        color = (199, 239, 145)
        draw.rectangle((left, top, right - 1, bottom - 1), outline=color, width=2)
        label = 'ANALYSIS AREA' if roi is not None else 'FULL FRAME'
        text = draw.textbbox((0, 0), label)
        label_width, label_height = text[2] + 8, text[3] + 6
        x = max(0, min(left + 4, image.width - label_width - 2))
        y = max(0, min(top + 4, image.height - label_height - 2))
        draw.rectangle((x, y, x + label_width, y + label_height), fill=(20, 35, 29))
        draw.text((x + 4, y + 3), label, fill=color)
        result = io.BytesIO()
        image.save(result, format='JPEG', quality=80)
        return self.send(result.getvalue(), 'image/jpeg')

    def live_frame(self):
        query = parse_qs(urlsplit(self.path).query)
        if 'check' in query:
            valid = LIVE.validate(query['check'][0])
            return self.send(json.dumps(dict(valid=valid)).encode(), 'application/json',
                             200 if valid else 503,
                             {'X-Camera-Stream-Error': '1'} if not valid else None)
        analysis_request = query.get('analysis') == ['1']
        if analysis_request:
            frame, token = LIVE.get(max_age=.2, wait_seconds=4, with_token=True,
                                    full_resolution=True)
        else:
            frame, token = browser_live().get(max_age=.5, wait_seconds=2), None
        headers = {'X-Camera-Frame-Token': token} if frame and token else None
        if headers and analysis_request:
            headers['X-Camera-Analysis-Source'] = ANALYSIS_SOURCE
        quality = LIVE.quality() if analysis_request else browser_live().quality()
        if not frame and quality['state'] == 'recovering':
            return self.send(b'Video stream error; damaged frames excluded', 'text/plain', 503,
                             {'X-Camera-Stream-Error': '1'})
        return self.send(frame or b'Live preview unavailable',
                         'image/jpeg' if frame else 'text/plain',
                         200 if frame else 503, headers)

    def download_clip(self, path):
        clip = clip_path(path.removeprefix('/api/download/'))
        with clip.open('rb') as clip_file:
            self.send_response(200)
            self.send_header('Content-Type', 'video/x-matroska')
            self.send_header('Content-Disposition', 'attachment; filename="'+clip.name+'"')
            self.send_header('Content-Length', str(clip.stat().st_size))
            self.end_headers()
            shutil.copyfileobj(clip_file, self.wfile)

    def play_clip(self, path):
        clip = clip_path(path.removeprefix('/api/play/'))
        # Fragmented MP4 allows playback without a second copy of each clip.
        process = subprocess.Popen([
            'ffmpeg', '-nostdin', '-loglevel', 'quiet', '-i', str(clip),
            '-map', '0:v:0', '-an', '-c:v', 'copy',
            '-movflags', 'frag_keyframe+empty_moov+default_base_moof',
            '-f', 'mp4', 'pipe:1'], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
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

    def route(self):
        path = unquote(urlsplit(self.path).path)
        if path == '/api/capture':
            result = dict(available=bool(source.capture_url()), settings=source.capture_settings())
            return self.send(json.dumps(result).encode(), 'application/json')
        if path == '/api/anomaly':
            return self.anomaly_status()
        if path.startswith('/api/anomaly/sessions/'):
            return self.learning_thumbnail(path.removeprefix('/api/anomaly/sessions/'))
        if path.startswith('/api/anomaly/events/'):
            event = anomaly_control.event_path(path.removeprefix('/api/anomaly/events/'))
            return self.send(event.read_bytes(), 'image/jpeg')
        if path == '/api/status':
            return self.send(json.dumps(status()).encode(), 'application/json')
        if path == '/api/live.jpg':
            return self.live_frame()
        if path.startswith('/api/download/'):
            return self.download_clip(path)
        if path.startswith('/api/play/'):
            return self.play_clip(path)
        if path in ASSETS:
            filename, content_type = ASSETS[path]
            return self.send((WEB / filename).read_bytes(), content_type)
        self.send(b'Not found', 'text/plain', 404)


if __name__ == '__main__':
    RECORDINGS.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer((os.environ.get('DASHBOARD_HOST', '0.0.0.0'), int(os.environ.get('DASHBOARD_PORT', '8082'))), Handler)
    print('Camera dashboard listening on port', server.server_port, flush=True)
    server.serve_forever()
