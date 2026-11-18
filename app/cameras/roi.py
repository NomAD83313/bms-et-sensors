"""Normalized full-source ROI validation and bounded model-input geometry."""
import math
from numbers import Real

try:
    from .model_profiles import input_dimensions
except ImportError:
    from model_profiles import input_dimensions

ROI_PREPROCESSING = 'roi-original-aspect-v1'


class ROIGeometryChanged(ValueError):
    """The selected region no longer matches the geometry of its learned bank."""


def validate_roi(value):
    """Return a canonical normalized rectangle, or None for the full frame."""
    if value is None:
        return None
    fields = ('x', 'y', 'width', 'height')
    if not isinstance(value, dict) or set(value) != set(fields):
        raise ValueError('ROI must contain x, y, width and height')
    result = {}
    for field in fields:
        number = value[field]
        if isinstance(number, bool) or not isinstance(number, Real):
            raise ValueError('ROI coordinates must be finite numbers')
        try:
            number = float(number)
        except (OverflowError, ValueError) as error:
            raise ValueError('ROI coordinates must be finite numbers') from error
        if not math.isfinite(number):
            raise ValueError('ROI coordinates must be finite numbers')
        result[field] = number
    x, y, width, height = (result[field] for field in fields)
    if (x < 0 or y < 0 or width < .01 or height < .01 or
            x >= 1 or y >= 1 or width > 1 or height > 1 or
            x + width > 1 + 1e-12 or y + height > 1 + 1e-12):
        raise ValueError('ROI must stay within the frame and each side must be at least 1%')
    # Ignore sub-pixel JSON roundoff without changing any practical source pixel.
    result = {field: round(number, 12) for field, number in result.items()}
    result['width'] = min(result['width'], 1 - result['x'])
    result['height'] = min(result['height'], 1 - result['y'])
    if result == dict(x=0, y=0, width=1, height=1):
        return None
    return result


def roi_bounds(source_width, source_height, roi):
    """Map normalized source coordinates to an inclusive/exclusive pixel box."""
    if any(isinstance(value, bool) or not isinstance(value, int) or value <= 0
           for value in (source_width, source_height)):
        raise ValueError('Source dimensions must be positive integers')
    roi = validate_roi(roi)
    if roi is None:
        return 0, 0, source_width, source_height
    # Decimal UI coordinates must not create a spurious extra boundary pixel.
    left = max(0, min(source_width - 1, math.floor(round(roi['x'] * source_width, 8))))
    top = max(0, min(source_height - 1, math.floor(round(roi['y'] * source_height, 8))))
    right = min(source_width, max(left + 1, math.ceil(round((roi['x'] + roi['width']) * source_width, 8))))
    bottom = min(source_height, max(top + 1, math.ceil(round((roi['y'] + roi['height']) * source_height, 8))))
    return left, top, right, bottom


def roi_input_dimensions(input_size, source_width, source_height, roi):
    """Shrink the crop to the model-input bounds without enlarging source pixels."""
    maximum_width, maximum_height = input_dimensions(input_size)
    roi = validate_roi(roi)
    bounds = roi_bounds(source_width, source_height, roi)
    if roi is None:
        return maximum_width, maximum_height
    crop_width, crop_height = bounds[2] - bounds[0], bounds[3] - bounds[1]
    scale = min(1, maximum_width / crop_width, maximum_height / crop_height)
    width = min(maximum_width, max(1, round(crop_width * scale)))
    height = min(maximum_height, max(1, round(crop_height * scale)))
    if min(width, height) < 32:
        raise ValueError('ROI aspect is too narrow for this Model Input; each model side must be at least 32 pixels')
    return width, height
