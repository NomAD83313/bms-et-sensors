"""Skip full analysis while the analysed area of the scene has not changed.

A small RGB thumbnail of the region of interest is compared
with the last frame that was fully evaluated. Small objects barely move a global average, so
the difference is judged per 8x8 block (the largest block difference) next to the overall
mean difference, which catches lighting drift. An unchanged frame reuses the previous
score and heatmap; any change, a source-geometry change, or a result older than
`max_age` seconds forces a full evaluation.
"""
import collections
import io
import math
import os
import time

try:
    from .roi import roi_bounds, validate_roi
except ImportError:
    from roi import roi_bounds, validate_roi

THUMBNAIL_WIDTH = 192
BLOCK = 8
# Measured on static Pi-camera frames: consecutive block maxima stay below 1.5 and the
# drift over two minutes below 3.2, while a 0.6%-wide object of weak contrast gives 7.5.
DEFAULT_BLOCK_DIFFERENCE = 3.0
DEFAULT_MEAN_DIFFERENCE = 1.2
DEFAULT_MAX_AGE_SECONDS = 2.0
RECENT_DECISIONS = 100


def environment_float(name, default, low, high):
    raw = os.environ.get(name)
    if raw is None or raw == '':
        return default
    try:
        value = float(raw)
    except ValueError:
        value = float('nan')
    if not low <= value <= high:
        raise ValueError(f'{name} must be a number between {low} and {high}')
    return value


def block_metrics(first, second):
    """Return (mean difference, largest 8x8 block mean difference) of two thumbnails."""
    import numpy as np
    difference = np.abs(first.astype(np.int16) - second.astype(np.int16))
    if difference.ndim == 3:
        # Keep changes in any RGB channel visible, even at equal luminance.
        difference = difference.max(axis=2)
    height, width = difference.shape
    rows, columns = np.arange(0, height, BLOCK), np.arange(0, width, BLOCK)
    sums = np.add.reduceat(np.add.reduceat(difference, rows, axis=0), columns, axis=1)
    areas = np.minimum(BLOCK, height - rows)[:, None] * np.minimum(BLOCK, width - columns)
    blocks = sums / areas
    return float(difference.mean()), float(blocks.max())


class FrameGate:
    def __init__(self, enabled=None, block_difference=None, mean_difference=None, max_age=None,
                 clock=time.monotonic):
        if enabled is None:
            enabled = os.environ.get('CAMERA_ANOMALY_FRAME_GATE', '1')
        if isinstance(enabled, str):
            if enabled not in ('0', '1'):
                raise ValueError('CAMERA_ANOMALY_FRAME_GATE must be 0 or 1')
            enabled = enabled == '1'
        self.enabled = bool(enabled)
        self.block_difference = (environment_float('CAMERA_ANOMALY_GATE_BLOCK_DIFF', DEFAULT_BLOCK_DIFFERENCE, .5, 50)
                                 if block_difference is None else block_difference)
        self.mean_difference = (environment_float('CAMERA_ANOMALY_GATE_MEAN_DIFF', DEFAULT_MEAN_DIFFERENCE, .2, 50)
                                if mean_difference is None else mean_difference)
        self.max_age = (environment_float('CAMERA_ANOMALY_GATE_MAX_AGE_SEC', DEFAULT_MAX_AGE_SECONDS, .5, 10)
                        if max_age is None else max_age)
        self.clock = clock
        self.skipped = self.evaluated = 0
        self.recent = collections.deque(maxlen=RECENT_DECISIONS)
        self.reset()

    def reset(self):
        """Forget the reference: the next frame is always fully evaluated."""
        self._thumbnail = self._source_size = self._result = None
        self._evaluated_at = float('-inf')

    @staticmethod
    def thumbnail(jpeg, roi):
        """RGB thumbnail and source size; None if undecodable."""
        try:
            import numpy as np
            from PIL import Image
            with Image.open(io.BytesIO(jpeg)) as image:
                source = image.size
                # JPEG DCT scaling decodes only 1/64 of the pixels.
                mode = 'RGB'
                image.draft(mode, (math.ceil(source[0] / 8), math.ceil(source[1] / 8)))
                image.load()
                width, height = image.size
                bounds = roi_bounds(*source, validate_roi(roi))
                box = (round(bounds[0] * width / source[0]), round(bounds[1] * height / source[1]),
                       max(round(bounds[2] * width / source[0]), round(bounds[0] * width / source[0]) + 1),
                       max(round(bounds[3] * height / source[1]), round(bounds[1] * height / source[1]) + 1))
                crop = image.crop(box)
                size = (THUMBNAIL_WIDTH, max(BLOCK, round(THUMBNAIL_WIDTH * crop.height / crop.width)))
                return np.asarray(crop.convert(mode).resize(size, Image.BOX), dtype=np.uint8), source
        except Exception:
            # The gate only saves work: any failure falls back to a full evaluation.
            return None

    def reusable(self, thumbnail):
        """Return the previous (score, heatmap) when the frame is unchanged, else None."""
        if not self.enabled or thumbnail is None or self._result is None:
            return None
        array, source = thumbnail
        if (source != self._source_size or array.shape != self._thumbnail.shape
                or self.clock() - self._evaluated_at >= self.max_age):
            return None
        mean, largest = block_metrics(self._thumbnail, array)
        if mean >= self.mean_difference or largest >= self.block_difference:
            return None
        return self._result

    def count(self, skipped):
        self.skipped += skipped
        self.evaluated += not skipped
        self.recent.append(bool(skipped))

    def remember(self, thumbnail, result):
        """Store a fully evaluated, validated frame as the new reference."""
        if not self.enabled or thumbnail is None:
            return
        self._thumbnail, self._source_size = thumbnail
        self._result = result
        self._evaluated_at = self.clock()

    def status(self):
        recent = len(self.recent)
        return dict(frame_gate_enabled=self.enabled, frames_skipped=self.skipped,
                    frames_evaluated=self.evaluated,
                    skip_ratio=round(sum(self.recent) / recent, 3) if recent else 0.0)
