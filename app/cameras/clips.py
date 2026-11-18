"""Clip retention and FFmpeg progress parsing shared by the recorder modes."""
import logging
import time

LOG = logging.getLogger("bms-cameras")


def prune(directory, max_bytes, max_age, now=None):
    """Remove oldest completed clips; never remove an active partial file."""
    now = time.time() if now is None else now
    clips = sorted(directory.glob("*.mkv"), key=lambda p: p.stat().st_mtime)
    total = sum(p.stat().st_size for p in clips)
    for path in clips:
        stat = path.stat()
        if now - stat.st_mtime > max_age or total > max_bytes:
            path.unlink()
            path.with_suffix('.json').unlink(missing_ok=True)
            total -= stat.st_size
            LOG.info("Removed expired clip %s", path.name)


class StreamProgress:
    """Report recording only while FFmpeg's video timestamp advances."""
    def __init__(self):
        self.buffer = b''
        self.timestamp = 0
        self.updated = None

    def feed(self, chunk, now):
        self.buffer += chunk
        while b'\n' in self.buffer:
            line, self.buffer = self.buffer.split(b'\n', 1)
            if line.startswith(b'out_time_us='):
                try:
                    timestamp = int(line.split(b'=', 1)[1])
                except ValueError:
                    continue
                if timestamp > self.timestamp:
                    self.timestamp = timestamp
                    self.updated = now

    def state(self, now):
        return 'recording' if self.updated is not None and now - self.updated < 6 else 'connecting'
