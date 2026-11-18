"""Persistent, bounded normal-view sessions shared by both camera services."""
from pathlib import Path
import re
import shutil
import time
import uuid

try:
    from .files import read_json, write_json
except ImportError:
    from files import read_json, write_json

MAX_SESSIONS = 8
MAX_FRAMES = 120
MAX_STORAGE_BYTES = 4 * 1024 ** 3


def session_id(value):
    if not isinstance(value, str) or not re.fullmatch(r'[a-f0-9]{32}', value):
        raise ValueError('Invalid learning session ID')
    return value


def session_text(value, maximum):
    if not isinstance(value, str) or len(value) > maximum:
        raise ValueError('Invalid learning session text')
    return value.strip()


def configuration(model):
    """Only combine sessions whose preprocessing and source geometry match."""
    return dict(model_key=model.model_key, input_size=model.input_size,
                color_mode='rgb',
                roi=getattr(model, 'roi', None),
                source_size=list(model.source_size) if getattr(model, 'source_size', None) else None,
                effective_input_size=list(model.effective_input_size)
                if getattr(model, 'effective_input_size', None) else None,
                jpeg_draft=getattr(model, 'jpeg_draft', False),
                score_top_k=getattr(model, 'top_k', 1))


COMPATIBILITY_KEYS = ('model_key', 'input_size', 'roi', 'jpeg_draft', 'score_top_k', 'color_mode')


def configuration_matches(current, catalog):
    """Return whether saved sessions were captured with the current model settings."""
    saved = catalog.get('configuration') or {}
    return all(current[key] == saved.get(key) for key in COMPATIBILITY_KEYS)


class LearningSessions:
    def __init__(self, data):
        self.root = Path(data) / 'learning'
        self.pending = None

    def recover(self):
        """Discard interrupted staging data; published sessions are never removed here."""
        keep = {item['id'] for item in self.catalog()['sessions']}
        for path in self.root.glob('*'):
            if path.is_symlink() or not path.is_dir() or path.name in keep:
                continue
            if re.fullmatch(r'(?:\.pending-)?[a-f0-9]{32}', path.name):
                shutil.rmtree(path)

    def clear(self):
        """Invalidate the catalog first, then remove only its validated session directories."""
        catalog = self.catalog()
        paths = [self.path(item['id']) for item in catalog['sessions']]
        self.abort()
        if (self.root / 'catalog.json').exists():
            write_json(self.root / 'catalog.json', dict(version=1, revision=None,
                       configuration=None, sessions=[]))
        for path in paths:
            if path.exists():
                shutil.rmtree(path)
        return len(paths)

    def catalog(self):
        path = self.root / 'catalog.json'
        if self.root.is_symlink() or path.is_symlink():
            raise ValueError('Invalid learning session directory')
        result = read_json(path)
        if result is None:
            if path.exists():
                raise ValueError('Learning session catalog is damaged')
            return dict(version=1, revision=None, configuration=None, sessions=[])
        if (result.get('version') != 1 or not isinstance(result.get('sessions'), list)
                or len(result['sessions']) > MAX_SESSIONS):
            raise ValueError('Invalid learning session catalog')
        for item in result['sessions']:
            session_id(item['id'])
        return result

    def path(self, identifier):
        path = self.root / session_id(identifier)
        if self.root.is_symlink() or path.is_symlink():
            raise ValueError('Invalid learning session directory')
        return path

    def public(self):
        return [{key: item.get(key) for key in
                 ('id', 'name', 'note', 'created', 'frames', 'calibration_frames', 'calibration_threshold')}
                for item in self.catalog()['sessions']]

    def check(self, mode, identifier=None):
        items = self.catalog()['sessions']
        if mode == 'add' and (not items or len(items) >= MAX_SESSIONS):
            raise ValueError('Create a new baseline first, or replace one of eight sessions')
        if mode in ('replace', 'remove', 'rename'):
            session_id(identifier)
            if not any(item['id'] == identifier for item in items):
                raise ValueError('Learning session not found')

    def begin(self, mode, name='', note='', identifier=None):
        if mode not in ('new', 'add', 'replace') or self.root.is_symlink():
            raise ValueError('Invalid learning operation')
        self.check(mode, identifier)
        self.abort()
        items = self.catalog()['sessions']
        old = next((item for item in items if item['id'] == identifier), None)
        number = 1 if mode == 'new' else old['number'] if old else max((item['number'] for item in items), default=0) + 1
        metadata = dict(id=uuid.uuid4().hex, number=number,
                        name=session_text(name, 80) or (old['name'] if old else f'Session {number}'),
                        note=session_text(note, 240), created=time.time(), frames=0, bytes=0)
        path = self.root / ('.pending-' + metadata['id'])
        path.mkdir(parents=True)
        self.pending = dict(path=path, metadata=metadata, mode=mode, replace=identifier,
                            configuration=None)

    def append(self, jpeg, config):
        pending = self.pending
        if pending is None or pending['metadata']['frames'] >= MAX_FRAMES:
            raise ValueError('No learning session can accept this frame')
        if pending['configuration'] is None:
            if (pending['mode'] != 'new' and self.catalog()['configuration'] != config):
                raise ValueError('Learning session settings changed; create a new baseline')
            pending['configuration'] = config
        if pending['configuration'] != config:
            raise ValueError('Camera geometry changed during learning')
        index = pending['metadata']['frames']
        stored_bytes = sum(item.get('bytes', 0) for item in self.catalog()['sessions'])
        if (stored_bytes + pending['metadata']['bytes'] + len(jpeg) > MAX_STORAGE_BYTES
                or shutil.disk_usage(self.root).free < len(jpeg) + 64 * 1024 ** 2):
            raise OSError('Learning storage limit reached')
        (pending['path'] / f'frame-{index:04d}.jpg').write_bytes(jpeg)
        pending['metadata']['frames'] += 1
        pending['metadata']['bytes'] += len(jpeg)

    def proposed(self, remove=None):
        items = self.catalog()['sessions']
        if remove is not None:
            self.check('remove', remove)
            return [item for item in items if item['id'] != remove]
        pending = self.pending
        if pending is None or pending['metadata']['frames'] < 20:
            raise ValueError('At least 20 complete frames are required')
        if pending['mode'] == 'new':
            return [dict(pending['metadata'])]
        if pending['mode'] == 'replace':
            return [dict(pending['metadata']) if item['id'] == pending['replace'] else dict(item)
                    for item in items]
        return [*map(dict, items), dict(pending['metadata'])]

    def groups(self, items):
        groups = []
        for item in items:
            path = (self.pending['path'] if self.pending and item['id'] == self.pending['metadata']['id']
                    else self.path(item['id']))
            groups.append([path / f'frame-{index:04d}.jpg' for index in range(item['frames'])])
        return groups

    def publish(self, items, revision):
        old = self.catalog()
        config = self.pending['configuration'] if self.pending else old['configuration']
        if self.pending:
            self.pending['path'].rename(self.path(self.pending['metadata']['id']))
        write_json(self.root / 'catalog.json', dict(version=1, revision=revision,
                   configuration=config, sessions=items))
        self.pending = None
        keep = {item['id'] for item in items}
        for item in old['sessions']:
            if item['id'] not in keep:
                shutil.rmtree(self.path(item['id']), ignore_errors=True)

    def rename(self, identifier, name, note):
        self.check('rename', identifier)
        catalog = self.catalog()
        for item in catalog['sessions']:
            if item['id'] == identifier:
                item['name'] = session_text(name, 80) or f"Session {item['number']}"
                item['note'] = session_text(note, 240)
        write_json(self.root / 'catalog.json', catalog)

    def abort(self):
        if self.pending:
            shutil.rmtree(self.pending['path'], ignore_errors=True)
            self.pending = None

    def thumbnail(self, identifier):
        self.check('rename', identifier)
        path = self.path(identifier) / 'frame-0000.jpg'
        if path.is_symlink():
            raise ValueError('Invalid learning frame')
        return path
