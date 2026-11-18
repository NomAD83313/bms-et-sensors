"""Shared recording controls and recorder heartbeat."""
import os
from pathlib import Path
import time

try:
    from .files import read_json, write_json
except ImportError:
    from files import read_json, write_json

CONTROL = Path(os.environ.get('CAMERA_CONTROL_DIR', '/control'))
HEARTBEAT_MAX_AGE_SECONDS = 15


def root(directory=None):
    """The camera's control directory; the module default is read at call time."""
    return CONTROL if directory is None else Path(directory)


def enabled(directory=None):
    return not (root(directory) / 'paused').exists()


def set_enabled(value, directory=None):
    directory = root(directory)
    directory.mkdir(parents=True, exist_ok=True)
    if value:
        (directory / 'paused').unlink(missing_ok=True)
    else:
        (directory / 'paused').touch()


def follow_detection(monitoring, mode, directory=None):
    """In event-only mode, the detection session owns the recording buffer."""
    if mode == 'anomaly' and enabled(directory) != bool(monitoring):
        set_enabled(bool(monitoring), directory)


def heartbeat(state, directory=None, **details):
    write_json(root(directory) / 'state.json', dict(details, state=state, updated=time.time()))


def recording_details(directory=None):
    data = read_json(root(directory) / 'state.json')
    try:
        return data if time.time() - data['updated'] < HEARTBEAT_MAX_AGE_SECONDS else {'state': 'offline'}
    except (TypeError, KeyError):
        return {'state': 'offline'}


def recording_state(directory=None):
    return recording_details(directory)['state']
