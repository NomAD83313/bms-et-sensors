"""Skip full analysis while the analysed area of the scene has not changed.

A small RGB thumbnail of the region of interest is compared
with the last frame that was fully evaluated. Small objects barely move a global average, so
the difference is judged per 8x8 block (the largest block difference) next to the overall
mean difference, which catches lighting drift. An unchanged frame reuses the previous
score and heatmap; any change, a source-geometry change, or a result older than
`max_age` seconds forces a full evaluation.

The thresholds adapt to each camera's noise. Every comparison of a frame the model judged
normal is a noise sample; the thresholds become the 95th percentile of those samples times
1.3, never below the configured values (a quiet camera keeps them) and never above the
limits, which stay far below the changes the model itself can detect (measured on a noisy
OV5647: the weakest object the model flagged gave a block difference of 31).
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
DEFAULT_BLOCK_DIFFERENCE_LIMIT = 12.0
DEFAULT_MEAN_DIFFERENCE_LIMIT = 4.0
RECENT_DECISIONS = 100
NOISE_WINDOW = 300
NOISE_MIN_SAMPLES = 60
NOISE_PERCENTILE = 95
NOISE_MARGIN = 1.3
NOISE_UPDATE_EVERY = 10


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
                 clock=time.monotonic, adaptive=None, block_limit=None, mean_limit=None):
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
        if adaptive is None:
            adaptive = os.environ.get('CAMERA_ANOMALY_GATE_ADAPTIVE', '1')
            if adaptive not in ('0', '1'):
                raise ValueError('CAMERA_ANOMALY_GATE_ADAPTIVE must be 0 or 1')
            adaptive = adaptive == '1'
        self.adaptive = bool(adaptive)
        # The configured thresholds are the floor; noise can raise them up to the limits.
        self.block_floor, self.mean_floor = self.block_difference, self.mean_difference
        self.block_limit = max(self.block_floor, environment_float(
            'CAMERA_ANOMALY_GATE_BLOCK_DIFF_MAX', DEFAULT_BLOCK_DIFFERENCE_LIMIT, .5, 50)
            if block_limit is None else block_limit)
        self.mean_limit = max(self.mean_floor, environment_float(
            'CAMERA_ANOMALY_GATE_MEAN_DIFF_MAX', DEFAULT_MEAN_DIFFERENCE_LIMIT, .2, 50)
            if mean_limit is None else mean_limit)
        self.noise = collections.deque(maxlen=NOISE_WINDOW)
        self._noise_geometry = None
        self._pending = None
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
        self._pending = None
        if not self.enabled or thumbnail is None or self._result is None:
            return None
        array, source = thumbnail
        if source != self._source_size or array.shape != self._thumbnail.shape:
            return None
        mean, largest = block_metrics(self._thumbnail, array)
        # Kept as a noise sample until the model's verdict on this frame is known.
        self._pending = (mean, largest)
        if (self.clock() - self._evaluated_at >= self.max_age
                or mean >= self.mean_difference or largest >= self.block_difference):
            return None
        return self._result

    def observe(self, normal):
        """Record the last comparison as noise when the frame was judged normal."""
        sample, self._pending = self._pending, None
        if not self.enabled or not self.adaptive or not normal or sample is None:
            return
        self.noise.append(sample)
        if len(self.noise) >= NOISE_MIN_SAMPLES and len(self.noise) % NOISE_UPDATE_EVERY == 0:
            self._adapt()

    def _adapt(self):
        import numpy as np
        means, blocks = np.asarray(self.noise, dtype=np.float64).T
        self.mean_difference = float(min(self.mean_limit, max(
            self.mean_floor, NOISE_MARGIN * np.percentile(means, NOISE_PERCENTILE))))
        self.block_difference = float(min(self.block_limit, max(
            self.block_floor, NOISE_MARGIN * np.percentile(blocks, NOISE_PERCENTILE))))

    def count(self, skipped):
        self.skipped += skipped
        self.evaluated += not skipped
        self.recent.append(bool(skipped))

    def remember(self, thumbnail, result):
        """Store a fully evaluated, validated frame as the new reference."""
        if not self.enabled or thumbnail is None:
            return
        geometry = (thumbnail[0].shape, thumbnail[1])
        if geometry != self._noise_geometry:
            # Another area or source: its noise must be learnt again from the floor.
            self._noise_geometry = geometry
            self.noise.clear()
            self.block_difference, self.mean_difference = self.block_floor, self.mean_floor
        self._thumbnail, self._source_size = thumbnail
        self._result = result
        self._evaluated_at = self.clock()

    def status(self):
        recent = len(self.recent)
        return dict(frame_gate_enabled=self.enabled, frames_skipped=self.skipped,
                    frames_evaluated=self.evaluated,
                    skip_ratio=round(sum(self.recent) / recent, 3) if recent else 0.0,
                    frame_gate_adaptive=self.adaptive, frame_gate_noise_samples=len(self.noise),
                    frame_gate_block_threshold=round(self.block_difference, 2),
                    frame_gate_mean_threshold=round(self.mean_difference, 2))
