"""Bounded decoder diagnostics and a short admission delay for preview frames."""
import uuid


def decoder_error_kind(line):
    """Classify known diagnostics without retaining or logging raw FFmpeg text."""
    text = line.lower()
    if any(value in text for value in (
        'corrupt decoded frame', 'packet corrupt', 'corrupt input packet',
        'error while decoding', 'error decoding', 'decode_slice_header error',
        'concealing ', 'missing reference', 'reference picture missing',
        'non-existing pps', 'non-existing sps', 'invalid nal', 'invalid data found',
        'cabac decode', 'bytestream', 'mmco: unref short failure',
        'left block unavailable', 'top block unavailable',
    )):
        return 'decode_error'
    if any(value in text for value in (
        'connection timed out', 'connection refused', 'connection reset',
        'network is unreachable', 'no route to host', 'input/output error',
        'server returned', 'could not find codec parameters', 'rtp: missed', 'bad cseq',
    )):
        return 'stream_error'
    return None


class DecodeGuard:
    """Called under the preview condition lock, including stderr callbacks."""
    def __init__(self, cooldown=1.0, settle=.02):
        self.cooldown, self.settle = cooldown, settle
        self.session = uuid.uuid4().hex
        self.errors = self.discarded = 0
        self.reject_until = 0
        self.last_error = self.last_error_at = None
        self.pending = None

    @property
    def token(self):
        return self.session + '-' + str(self.errors)

    def recovering(self, now):
        return now < self.reject_until

    def note_error(self, kind, now, wall_time):
        self.errors += 1
        self.last_error, self.last_error_at = kind, wall_time
        self.reject_until = now + self.cooldown
        if self.pending is not None:
            self.discarded += 1
        self.pending = None

    def feed(self, frame, now):
        if self.recovering(now):
            self.discarded += 1
            return None
        previous = self.pending
        self.pending = (frame, now, self.token)
        if previous is not None and now - previous[1] >= self.settle:
            return previous
        if previous is not None:
            self.discarded += 1
        return None

    def release(self, now):
        """Admit a settled frame without waiting for the next low-rate source frame."""
        if self.recovering(now) or self.pending is None or now - self.pending[1] < self.settle:
            return None
        accepted, self.pending = self.pending, None
        return accepted
