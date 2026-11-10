"""FFmpeg input options shared by network and local Pi camera streams."""
import os
from urllib.parse import urlsplit, urlunsplit

_CAPTURE_CACHE = None
_CAPTURE_CACHE_AT = 0


def input_args(url):
    parsed = urlsplit(url)
    if parsed.scheme == 'rtsp' and parsed.hostname:
        return ['-rtsp_transport', 'tcp', '-timeout', '15000000', '-i', url]
    if parsed.scheme == 'http' and parsed.hostname == '127.0.0.1' and parsed.path == '/stream.ts':
        return ['-rw_timeout', '15000000', '-i', url]
    raise ValueError('Use an RTSP camera URL or the loopback Pi camera /stream.ts endpoint')


def preview_input_args(url, transport='tcp', strict=True):
    """Return input options that favor the newest frame for live preview."""
    parsed = urlsplit(url)
    if parsed.scheme == 'rtsp' and parsed.hostname:
        if transport not in ('tcp', 'udp'):
            raise ValueError('Unsupported RTSP preview transport')
        args = [
            '-fflags', '+discardcorrupt+nobuffer',
            '-flags', 'low_delay',
        ]
        if strict:
            args += ['-err_detect', 'explode']
        return args + [
            '-analyzeduration', '0',
            '-probesize', '32768',
            '-max_delay', '0',
            '-reorder_queue_size', '0',
            '-rtsp_transport', transport,
            '-timeout', '3000000',
            '-i', url,
        ]
    return ['-fflags', '+discardcorrupt', '-err_detect', 'explode', *input_args(url)]


def browser_stream_url(url):
    """Return an optional RTSP substream URL without duplicating credentials."""
    path = os.environ.get('CAMERA_PREVIEW_PATH', '').strip()
    parsed = urlsplit(url)
    if not path or parsed.scheme != 'rtsp' or not parsed.hostname:
        return url
    if not path.startswith('/') or '?' in path or '#' in path:
        raise ValueError('CAMERA_PREVIEW_PATH must be an absolute URL path')
    return urlunsplit(parsed._replace(path=path, query='', fragment=''))


def capture_url():
    return os.environ.get('CAMERA_CAPTURE_URL', '').rstrip('/')


def capture_settings():
    global _CAPTURE_CACHE, _CAPTURE_CACHE_AT
    import time
    import json
    from pathlib import Path
    import os
    from urllib.request import urlopen
    try:
        from .capture_settings import validate, DEFAULTS
    except ImportError:
        from capture_settings import validate, DEFAULTS
    if not capture_url():
        return dict(DEFAULTS)
    path = Path(os.environ.get('CAMERA_CONTROL_DIR', '/control')) / 'capture.json'
    if path.exists():
        return validate(json.loads(path.read_text()))
    if _CAPTURE_CACHE is not None and time.monotonic()-_CAPTURE_CACHE_AT < 2:
        return dict(_CAPTURE_CACHE)
    try:
        with urlopen(capture_url() + '/api/config', timeout=2) as response:
            _CAPTURE_CACHE = validate(json.load(response))
    except OSError:
        _CAPTURE_CACHE = dict(DEFAULTS)
    _CAPTURE_CACHE_AT = time.monotonic()
    return dict(_CAPTURE_CACHE)


def capture_signature(settings=None):
    import json
    return json.dumps(capture_settings() if settings is None else settings, sort_keys=True)


def configure_capture(data):
    import json
    from pathlib import Path
    import os
    from urllib.request import Request, urlopen
    try:
        from .anomaly_control import write_json
    except ImportError:
        from anomaly_control import write_json
    if not capture_url():
        raise ValueError('Capture controls are only available for the Pi camera')
    request = Request(capture_url() + '/api/config', data=json.dumps(data).encode(),
        headers={'Content-Type': 'application/json', 'X-Camera-Control': '1'}, method='POST')
    with urlopen(request, timeout=10) as response:
        settings = json.load(response)
    write_json(Path(os.environ.get('CAMERA_CONTROL_DIR', '/control')) / 'capture.json', settings)
    return settings


def snapshot(after=None):
    import time
    import math
    from urllib.request import Request, urlopen
    request = Request(capture_url() + '/snapshot.jpg', headers={'X-Camera-After': after} if after else {})
    with urlopen(request, timeout=6) as response:
        jpeg = response.read(16*1024*1024+1)
        captured_at = float(response.headers['X-Camera-Captured-At'])
        token = response.headers['X-Camera-Sequence']
    maximum_age = max(1, capture_settings()['snapshot_interval_ms']/500)
    if (not token or not math.isfinite(captured_at) or not -.5 <= time.time()-captured_at <= maximum_age
            or len(jpeg) > 16*1024*1024 or not jpeg.startswith(b'\xff\xd8') or not jpeg.endswith(b'\xff\xd9')):
        raise ValueError('Snapshot is incomplete or stale')
    return jpeg, captured_at, token
