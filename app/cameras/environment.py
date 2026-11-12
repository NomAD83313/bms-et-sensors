"""Camera container settings read from the environment, with one default per setting.

The values are read at call time so tests and the shared analysis worker can change
them per camera.
"""
import os


def recording_mode():
    """'continuous' records clips all the time; 'anomaly' records events only."""
    return os.environ.get('CAMERA_RECORDING_MODE', 'continuous')


def event_windows():
    """Seconds of video kept before and after an anomaly event."""
    return (float(os.environ.get('CAMERA_EVENT_PRE_SEC', '7')),
            float(os.environ.get('CAMERA_EVENT_POST_SEC', '10')))


def clip_seconds():
    return int(os.environ.get('CLIP_SECONDS', '60'))


def max_storage_bytes():
    return int(os.environ.get('MAX_STORAGE_MB', '10240')) * 1024 * 1024


def max_age_hours():
    return float(os.environ.get('MAX_AGE_HOURS', '24'))
