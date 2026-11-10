"""File-based controls for the optional visual anomaly worker."""
import json
import os
from pathlib import Path
import time
import uuid

try:
    from .model_profiles import DEFAULT_MODEL, MODELS, validate_model, heatmap_dimensions
    from .roi import validate_roi
except ImportError:
    from model_profiles import DEFAULT_MODEL, MODELS, validate_model, heatmap_dimensions
    from roi import validate_roi

INPUT_SIZES = ('768x432', '1024x576', '1280x720', '1536x864', '1920x1080')
DEFAULT_INPUT_SIZE = INPUT_SIZES[0]
ACTIVE_STATES = frozenset(('normal', 'suspect', 'anomaly'))
STATUS_MAX_AGE_SECONDS = 20
LEARNING_SECONDS = (60, 120, 180, 240)

DATA = Path(os.environ.get('CAMERA_ANOMALY_DIR', '/anomaly'))


def read_json(path, default=None):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    temporary.write_text(json.dumps(value, allow_nan=False))
    temporary.replace(path)


def validate_multiplier(value):
    value = float(value)
    if not .1 <= value <= 20:
        raise ValueError('Threshold multiplier must be between 0.1 and 20')
    return value


def validate_input_size(value):
    if not isinstance(value, str) or value not in INPUT_SIZES:
        raise ValueError('Select a supported 16:9 model input')
    return value


def input_dimensions(value):
    return tuple(map(int, validate_input_size(value).split('x')))


def state_is_current(state, now=None):
    """Return whether a worker status has a valid recent timestamp."""
    now = time.time() if now is None else now
    updated = state.get('updated')
    if isinstance(updated, bool) or not isinstance(updated, (int, float)):
        return False
    age = now - updated
    return 0 <= age < STATUS_MAX_AGE_SECONDS


def analysis_available(state, now=None):
    """Return whether event recording may trust the current analysis state."""
    return state.get('state') in ACTIVE_STATES and state_is_current(state, now)


def validate_confirmation_frames(value):
    if isinstance(value, bool) or value not in (1, 2, 3, 4, 5):
        raise ValueError('Confirmation must be between 1 and 5 frames')
    return int(value)


def validate_learning_seconds(value):
    if isinstance(value, bool) or not isinstance(value, int) or value not in LEARNING_SECONDS:
        raise ValueError('Learning time must be 1, 2, 3 or 4 minutes')
    return int(value)


def command_mode(action, ready, learning, monitoring):
    if action == 'learn':
        return True, False
    if action == 'monitor':
        return False, ready
    if action in ('pause', 'resize', 'model', 'roi'):
        return False, False
    if action in ('configure', 'confirmation'):
        return learning, monitoring
    raise ValueError('Unknown analysis action')


def request(action, multiplier=1.25, input_size=None, confirmation_frames=None, model_key=None,
            roi=None, learning_seconds=None):
    command_mode(action, False, False, False)
    multiplier = validate_multiplier(multiplier)
    command = dict(id=uuid.uuid4().hex, action=action, multiplier=multiplier)
    if action == 'learn' and learning_seconds is not None:
        command['learning_seconds'] = validate_learning_seconds(learning_seconds)
    if action == 'resize':
        command['input_size'] = validate_input_size(input_size)
    if action == 'confirmation':
        command['confirmation_frames'] = validate_confirmation_frames(confirmation_frames)
    if action == 'model':
        command['model_key'] = validate_model(model_key)
    if action == 'roi':
        command['roi'] = validate_roi(roi)
    write_json(DATA / 'command.json', command)
    return command


def status():
    state = read_json(DATA / 'status.json', {})
    if not state_is_current(state):
        state = dict(state, state='offline', message='Visual analysis service is unavailable')
    events = []
    saved = sorted(DATA.glob('events/*.json'), reverse=True)
    for path in saved[:20]:
        event = read_json(path)
        if event:
            events.append(event)
    return dict(state, events=events, event_count=len(saved))


def event_path(name):
    if Path(name).name != name or not name.endswith('.jpg'):
        raise ValueError('Invalid event image')
    path = DATA / 'events' / name
    if path.is_symlink() or not path.is_file():
        raise ValueError('Event image not found')
    return path


class Confirmation:
    """Require consecutive high scores and space snapshots apart."""
    def __init__(self, required=3, cooldown=30):
        self.required = required
        self.cooldown = cooldown
        self.count = 0
        self.last_event = float('-inf')

    def update(self, score, threshold, now):
        self.count = self.count + 1 if score > threshold else 0
        if not self.count:
            # The cooldown limits one continuing episode, not a new separate anomaly.
            self.last_event = float('-inf')
        confirmed = self.count >= self.required
        event = confirmed and now - self.last_event >= self.cooldown
        if event:
            self.last_event = now
        return confirmed, event
