"""Shared recording controls and recorder heartbeat."""
import json
import os
from pathlib import Path
import time

CONTROL = Path(os.environ.get('CAMERA_CONTROL_DIR', '/control'))


def enabled():
    return not (CONTROL / 'paused').exists()


def set_enabled(value):
    CONTROL.mkdir(parents=True, exist_ok=True)
    if value:
        (CONTROL / 'paused').unlink(missing_ok=True)
    else:
        (CONTROL / 'paused').touch()


def follow_detection(monitoring, mode):
    """In event-only mode, the detection session owns the recording buffer."""
    if mode == 'anomaly' and enabled() != bool(monitoring):
        set_enabled(bool(monitoring))


def heartbeat(state, **details):
    CONTROL.mkdir(parents=True, exist_ok=True)
    temporary = CONTROL / 'state.tmp'
    temporary.write_text(json.dumps(dict(details, state=state, updated=time.time())))
    temporary.replace(CONTROL / 'state.json')


def recording_details():
    try:
        data = json.loads((CONTROL / 'state.json').read_text())
        return data if time.time() - data['updated'] < 15 else {'state': 'offline'}
    except (OSError, ValueError, KeyError):
        return {'state': 'offline'}


def recording_state():
    return recording_details()['state']
