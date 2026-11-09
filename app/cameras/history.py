"""Delete completed camera history without touching live buffers or models."""
from pathlib import Path
import re
import threading

LOCK = threading.Lock()


def delete_event(anomaly, event_id):
    if not isinstance(event_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', event_id):
        raise ValueError('Invalid event ID')
    directory = Path(anomaly) / 'events'
    if directory.is_symlink():
        raise ValueError('Invalid events directory')
    paths = [directory / (event_id + suffix) for suffix in ('.json', '.jpg', '-heat.jpg')]
    if any(path.is_symlink() for path in paths):
        raise ValueError('Symlink event files are not supported')
    found = any(path.is_file() for path in paths)
    for path in paths:
        path.unlink(missing_ok=True)
    return int(found)


def delete_recording(recordings, name):
    if not isinstance(name, str) or Path(name).name != name or not name.endswith('.mkv'):
        raise ValueError('Invalid recording name')
    path = Path(recordings) / name
    if path.is_symlink():
        raise ValueError('Symlink recordings are not supported')
    found = path.is_file()
    path.unlink(missing_ok=True)
    path.with_suffix('.json').unlink(missing_ok=True)
    return int(found)


def delete_saved(recordings, anomaly, action, name=None):
    if action not in ('event', 'recording', 'all-events', 'all-recordings', 'history'):
        raise ValueError('Invalid deletion action')
    result = dict(events=0, recordings=0)
    with LOCK:
        if action == 'event':
            result['events'] = delete_event(anomaly, name)
        elif action == 'recording':
            result['recordings'] = delete_recording(recordings, name)
        else:
            # Snapshot the file list once: newly created history is not removed.
            events = list((Path(anomaly) / 'events').glob('*.json')) if action in ('all-events', 'history') else []
            recordings = list(Path(recordings).glob('*.mkv')) if action in ('all-recordings', 'history') else []
            for path in events:
                if not path.is_symlink():
                    result['events'] += delete_event(anomaly, path.stem)
            for path in recordings:
                if not path.is_symlink():
                    result['recordings'] += delete_recording(path.parent, path.name)
    return result
