"""Validated capture profiles shared by the Pi host and camera application."""
import math

DEFAULTS = dict(capture_mode='video', width=1280, height=720, fps=15,
                bitrate=2000000, snapshot_interval_ms=200, shutter_us=0)
INTERVALS = (100, 200, 500, 1000)
# Sony IMX415 (3864x2192 full readout, at most 15.75 fps): 16:9 outputs that keep the
# full sensor field of view. The largest output is the full-width 4K picture.
MAX_WIDTH, MAX_HEIGHT = 3864, 2192
VIDEO_MAX_FPS = {(640, 360): 15, (1280, 720): 15, (1920, 1080): 15,
                 (2560, 1440): 15, (3840, 2160): 15}
UPDATE_FIELDS = {'capture_mode', 'snapshot_interval_ms', 'shutter_us', 'width', 'height', 'fps'}
SHUTTERS = (0, 200, 500, 1000, 2000, 4000, 8000, 10000, 20000, 40000)


def validate(data):
    result = dict(DEFAULTS, **data)
    for key in ('width', 'height', 'bitrate', 'snapshot_interval_ms', 'shutter_us'):
        value = result[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or int(value) != value:
            raise ValueError('Capture settings must contain finite numeric values')
        result[key] = int(value)
    if (result['capture_mode'] not in ('video', 'snapshots')
            or not 16 <= result['width'] <= MAX_WIDTH or not 16 <= result['height'] <= MAX_HEIGHT
            or result['width'] % 2 or result['height'] % 2
            or isinstance(result['fps'], bool) or not isinstance(result['fps'], (int, float))
            or not math.isfinite(result['fps']) or not 1 <= result['fps'] <= 240
            or not 100000 <= result['bitrate'] <= 50000000
            or result['snapshot_interval_ms'] not in INTERVALS
            or result['shutter_us'] not in SHUTTERS
            or (result['capture_mode'] == 'video' and
                (VIDEO_MAX_FPS.get((result['width'], result['height'])) is None
                 or result['fps'] > VIDEO_MAX_FPS[(result['width'], result['height'])]))):
        raise ValueError('Invalid capture mode, resolution, frame rate or exposure')
    period_us = result['snapshot_interval_ms'] * 1000 if result['capture_mode'] == 'snapshots' else 1e6 / result['fps']
    if result['shutter_us'] > period_us:
        raise ValueError('Exposure exceeds frame interval; select a shorter exposure or Auto')
    return {key: result[key] for key in DEFAULTS}


def validate_update(current, data):
    if not isinstance(data, dict) or set(data) - UPDATE_FIELDS:
        raise ValueError('Unsupported capture setting')
    result = validate(dict(current, **data))
    if result['capture_mode'] == 'video' and {'width', 'height', 'fps'} & set(data):
        maximum = VIDEO_MAX_FPS.get((result['width'], result['height']))
        requested_fps = data.get('fps', result['fps'])
        if maximum is None or requested_fps > maximum:
            raise ValueError('Unsupported video resolution or frame rate')
    return result
