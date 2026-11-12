"""File-based controls for the optional visual anomaly worker."""
import os
from pathlib import Path
import re
import time
import uuid

try:
    from .files import read_json, write_json
    from .learning_sessions import session_id as validate_session_id, session_text
    from .model_profiles import (DEFAULT_INPUT_SIZE, DEFAULT_MODEL, INPUT_SIZES, MODELS, heatmap_dimensions,
                                 input_dimensions, validate_color_mode, validate_input_size, validate_model)
    from .roi import validate_roi
except ImportError:
    from files import read_json, write_json
    from learning_sessions import session_id as validate_session_id, session_text
    from model_profiles import (DEFAULT_INPUT_SIZE, DEFAULT_MODEL, INPUT_SIZES, MODELS, heatmap_dimensions,
                                input_dimensions, validate_color_mode, validate_input_size, validate_model)
    from roi import validate_roi

# Re-exported for the worker and tests, which read model and file helpers from this module.
__all__ = ['DEFAULT_INPUT_SIZE', 'DEFAULT_MODEL', 'INPUT_SIZES', 'MODELS', 'heatmap_dimensions', 'input_dimensions',
           'read_json', 'validate_color_mode', 'validate_input_size', 'validate_model', 'write_json']

ACTIVE_STATES = frozenset(('normal', 'suspect', 'anomaly'))
STATUS_MAX_AGE_SECONDS = 20
LEARNING_SECONDS = (60, 120, 180, 240)
DEFAULT_MULTIPLIER = 1.25
SESSION_ACTIONS = ('learn', 'add-session', 'replace-session', 'remove-session',
                   'rename-session', 'rebuild-sessions')
# Every analysis action and the (learning, monitoring) mode it leaves the worker in.
# None keeps the current mode; 'ready' means monitoring resumes only with a baseline.
MODE_AFTER_ACTION = {
    'learn': (True, False), 'add-session': (True, False), 'replace-session': (True, False),
    'monitor': (False, 'ready'),
    'pause': (False, False), 'invalidate': (False, False),
    'resize': (False, False), 'model': (False, False), 'roi': (False, False), 'color': (False, False),
    'remove-session': (False, False), 'rebuild-sessions': (False, False),
    'configure': None, 'confirmation': None, 'rename-session': None,
}
# Actions that only take effect when they change a model setting.
CONFIGURATION_ACTIONS = ('resize', 'model', 'roi', 'color')
# Actions the dashboard accepts over HTTP; 'invalidate' is issued internally on a capture change.
PUBLIC_ACTIONS = frozenset(MODE_AFTER_ACTION) - {'invalidate'}
# Actions after which detection is off, so event-only recording must stop its buffer too.
MONITORING_STOPPING_ACTIONS = frozenset(
    action for action, mode in MODE_AFTER_ACTION.items() if mode is not None and mode[1] is False)

DATA = Path(os.environ.get('CAMERA_ANOMALY_DIR', '/anomaly'))


EVENT_ID = re.compile(r'[A-Za-z0-9_-]{1,100}')


def valid_event_id(value):
    """Event and trigger identifiers become file names, so keep them to a safe alphabet."""
    return isinstance(value, str) and EVENT_ID.fullmatch(value) is not None


def validate_multiplier(value):
    value = float(value)
    if not .1 <= value <= 20:
        raise ValueError('Threshold multiplier must be between 0.1 and 20')
    return value


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
    """Return the (learning, monitoring) mode after `action`; see MODE_AFTER_ACTION."""
    if action not in MODE_AFTER_ACTION:
        raise ValueError('Unknown analysis action')
    mode = MODE_AFTER_ACTION[action]
    if mode is None:
        return learning, monitoring
    return mode[0], ready if mode[1] == 'ready' else mode[1]


def request(action, multiplier=DEFAULT_MULTIPLIER, input_size=None, confirmation_frames=None, model_key=None,
            roi=None, learning_seconds=None, session_id=None, session_name='', session_note='',
            color_mode=None):
    command_mode(action, False, False, False)
    multiplier = validate_multiplier(multiplier)
    command = dict(id=uuid.uuid4().hex, action=action, multiplier=multiplier)
    if action in ('learn', 'add-session', 'replace-session') and learning_seconds is not None:
        command['learning_seconds'] = validate_learning_seconds(learning_seconds)
    if action in ('learn', 'add-session', 'replace-session', 'remove-session', 'rename-session'):
        if action in ('replace-session', 'remove-session', 'rename-session'):
            command['session_id'] = validate_session_id(session_id)
        command.update(session_name=session_text(session_name, 80),
                       session_note=session_text(session_note, 240))
    if action == 'resize':
        command['input_size'] = validate_input_size(input_size)
    if action == 'confirmation':
        command['confirmation_frames'] = validate_confirmation_frames(confirmation_frames)
    if action == 'model':
        command['model_key'] = validate_model(model_key)
    if action == 'roi':
        command['roi'] = validate_roi(roi)
    if action == 'color':
        command['color_mode'] = validate_color_mode(color_mode)
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
