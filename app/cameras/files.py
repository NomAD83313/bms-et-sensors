"""Atomic JSON and text file helpers shared by the camera modules (standard library only)."""
import json
import uuid


def read_json(path, default=None):
    """Return the parsed JSON file, or `default` when it is missing or damaged."""
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


def write_text(path, text):
    """Replace the file atomically so a reader never sees a partial write."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    temporary.write_text(text)
    temporary.replace(path)


def write_json(path, value):
    write_text(path, json.dumps(value, allow_nan=False))
