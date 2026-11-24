"""Continuous recording: back-to-back raw FIFO captures of one sensor in one record.

The sensor samples only while a capture runs and is silent while it sends the data
frame, so a continuous record is a series of 1024-sample captures with short gaps
(about the transfer time of the data frame). The captures are never joined into one
signal: each one is analysed on its own (mean removal, integration, FFT) and placed on
a common time axis using the first-sample time measured from its progress frames.
Pure functions, no numpy.
"""

from __future__ import annotations

import base64
import math
from typing import Any, Sequence

try:
    from . import vibration_fft as fft
except ImportError:  # Flat import inside the container.
    import vibration_fft as fft

AXES = fft.AXES
QUANTITIES = ("acc_g", "velocity_mm_s", "displacement_um")
# Time bins of the min/max envelope over the whole record (enough for a wide chart).
ENVELOPE_BINS = 1200
# Spectrogram cells are sent as 8-bit codes over this range below the loudest cell.
SPECTROGRAM_RANGE_DB = 80.0
SPECTROGRAM_FLOOR_G = 1e-9


def capture_starts(captures: Sequence[dict[str, Any]]) -> list[float]:
    """First-sample time of every capture in seconds after the first capture's.

    Uses the bus clock at each request plus the first-sample offset fitted from the
    progress frames. A capture without a usable fit takes the median offset of the
    others (the offset is mostly constant request and USB latency).
    """
    if not captures:
        return []
    offsets = [capture.get("first_sample_s") for capture in captures]
    known = sorted(offset for offset in offsets if offset is not None)
    median = known[len(known) // 2] if known else 0.0
    absolute = [float(capture.get("requested_at") or 0.0) + (median if offset is None else offset)
                for capture, offset in zip(captures, offsets)]
    return [round(value - absolute[0], 4) for value in absolute]


def gaps(starts: Sequence[float], capture_s: float) -> list[float]:
    """Pause between the end of one capture and the start of the next, in seconds."""
    return [max(0.0, starts[i + 1] - (starts[i] + capture_s)) for i in range(len(starts) - 1)]


def summary(starts: Sequence[float], samples: int, sample_rate: float) -> dict[str, Any]:
    """Span and coverage of a continuous record (stored with the record)."""
    capture_s = samples / sample_rate
    span = (starts[-1] if starts else 0.0) + capture_s
    pauses = gaps(starts, capture_s)
    return {
        "span_s": round(span, 3),
        "coverage": round(len(starts) * capture_s / span, 4) if span > 0 else 0.0,
        "gap_mean_s": round(sum(pauses) / len(pauses), 4) if pauses else 0.0,
        "gap_max_s": round(max(pauses), 4) if pauses else 0.0,
    }


def _round(values: Sequence[float | None], digits: int) -> list[float | None]:
    return [None if value is None else round(value, digits) for value in values]


def _capture_metrics(wave: dict[str, Any], result: dict[str, Any]) -> dict[str, dict[str, float | None]]:
    metrics: dict[str, dict[str, float | None]] = {}
    for axis in AXES:
        acc = wave["axes"][axis]["acc_g"]
        rms = math.sqrt(sum(a * a for a in acc) / len(acc))
        peak = max(abs(a) for a in acc)
        data = result["axes"][axis]
        metrics[axis] = {
            "acc_rms_g": round(rms, 6),
            "acc_peak_g": round(peak, 6),
            "crest": round(peak / rms, 3) if rms > 0 else None,
            "velocity_rms_mm_s": round(data["velocity_rms_mm_s"], 4),
            "peak_hz": round(data["peaks"][0][0], 3) if data["peaks"] else None,
            "peak_g": round(data["peaks"][0][1], 6) if data["peaks"] else None,
            # Up to three strongest FFT peaks above 2 Hz as [Hz, g] (frequency-over-time chart).
            "peaks": [[round(hz, 3), round(g, 6)] for hz, g in data["peaks"]],
        }
    return metrics


def _envelope_update(envelope: dict[str, Any], values: Sequence[float], bin_of: Sequence[int]) -> None:
    """Merge min/max of consecutive samples that fall into the same time bin."""
    lows, highs = envelope["min"], envelope["max"]
    start = 0
    count = len(values)
    while start < count:
        current = bin_of[start]
        end = start + 1
        while end < count and bin_of[end] == current:
            end += 1
        chunk = values[start:end]
        low, high = min(chunk), max(chunk)
        if lows[current] is None or low < lows[current]:
            lows[current] = low
        if highs[current] is None or high > highs[current]:
            highs[current] = high
        start = end


def _encode_db(columns: list[list[float]]) -> dict[str, Any]:
    """Spectrogram amplitudes as 8-bit codes of dB re 1 g, base64, column-major."""
    loudest = max((value for column in columns for value in column), default=SPECTROGRAM_FLOOR_G)
    db_max = math.ceil(20 * math.log10(max(loudest, SPECTROGRAM_FLOOR_G)))
    db_min = db_max - SPECTROGRAM_RANGE_DB
    codes = bytearray()
    for column in columns:
        for value in column:
            db = 20 * math.log10(max(value, SPECTROGRAM_FLOOR_G))
            codes.append(max(0, min(255, round((db - db_min) / SPECTROGRAM_RANGE_DB * 255))))
    return {"db_min": db_min, "db_max": db_max, "data": base64.b64encode(bytes(codes)).decode("ascii")}


def analyze(raw_captures: Sequence[Sequence[Sequence[float]]], starts: Sequence[float], sample_rate: float,
            window: int = 256, velocity_min_hz: float = fft.VELOCITY_RMS_MIN_HZ,
            envelope_bins: int = ENVELOPE_BINS) -> dict[str, Any]:
    """Time envelope, per-capture trends, spectrogram and peak-hold spectrum.

    Every capture is processed on its own; the spectrogram uses Hann windows of
    `window` samples with 50 % overlap inside each capture, never across a gap.
    """
    if not raw_captures:
        raise ValueError("at least one capture is required")
    if len(starts) != len(raw_captures):
        raise ValueError("one start time per capture is required")
    n = len(raw_captures[0])
    if window < 8 or window & (window - 1) or window > n:
        raise ValueError(f"window must be a power of two between 8 and {n}")
    if sample_rate <= 0:
        raise ValueError("sample rate must be positive")
    capture_s = n / sample_rate
    span = starts[-1] + capture_s
    bin_s = span / envelope_bins
    envelope = {quantity: {axis: {"min": [None] * envelope_bins, "max": [None] * envelope_bins} for axis in AXES}
                for quantity in QUANTITIES}
    hop = window // 2
    columns: dict[str, list[list[float]]] = {axis: [] for axis in AXES}
    column_times: list[float] = []
    peak_hold = {axis: [0.0] * (n // 2 + 1) for axis in AXES}
    captures_out: list[dict[str, Any]] = []
    for index, (capture, start) in enumerate(zip(raw_captures, starts)):
        if len(capture) != n:
            raise ValueError("captures must have the same length")
        wave = fft.waveforms(capture, sample_rate)
        result = fft.analyze(capture, sample_rate, velocity_min_hz=velocity_min_hz)
        captures_out.append({"index": index, "start_s": round(start, 4), "end_s": round(start + capture_s, 4),
                             "axes": _capture_metrics(wave, result)})
        bin_of = [min(envelope_bins - 1, int((start + i / sample_rate) / bin_s)) for i in range(n)]
        for quantity in QUANTITIES:
            for axis in AXES:
                _envelope_update(envelope[quantity][axis], wave["axes"][axis][quantity], bin_of)
        for axis in AXES:
            hold = peak_hold[axis]
            for k, value in enumerate(result["axes"][axis]["acc_g"]):
                if value > hold[k]:
                    hold[k] = value
        for offset in range(0, n - window + 1, hop):
            column_times.append(round(start + (offset + window / 2) / sample_rate, 4))
            for axis_index, axis in enumerate(AXES):
                columns[axis].append(fft.amplitude_spectrum([s[axis_index] for s in capture[offset:offset + window]]))
    digits = {"acc_g": 6, "velocity_mm_s": 4, "displacement_um": 3}
    return {
        "sample_rate_hz": round(sample_rate, 3), "samples": n, "capture_s": round(capture_s, 4),
        "span_s": round(span, 4), "capture_count": len(raw_captures),
        "gaps_s": [round(gap, 4) for gap in gaps(starts, capture_s)],
        "captures": captures_out,
        "envelope": {
            "bins": envelope_bins, "bin_s": bin_s,
            "quantities": {quantity: {axis: {key: _round(values, digits[quantity]) for key, values in data.items()}
                                      for axis, data in axes.items()} for quantity, axes in envelope.items()},
        },
        "spectrogram": {
            "window": window, "hop": hop, "df_hz": round(sample_rate / window, 5),
            "bins": window // 2 + 1, "column_s": round(hop / sample_rate, 5), "times_s": column_times,
            "axes": {axis: _encode_db(columns[axis]) for axis in AXES},
        },
        "peak_hold": {"df_hz": round(sample_rate / n, 5), "nyquist_hz": round(sample_rate / 2, 3),
                      "axes": {axis: {"acc_g": _round(values, 6)} for axis, values in peak_hold.items()}},
    }
