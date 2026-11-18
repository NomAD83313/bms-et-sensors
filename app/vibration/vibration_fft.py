"""Spectrum of one raw acceleration FIFO capture: pure functions, no numpy.

Processing per axis: remove the mean (gravity and offset), apply a Hann window,
radix-2 FFT, single-sided amplitude spectrum in g (corrected for the window's
coherent gain, so a sine of amplitude A shows a peak of about A). The velocity
spectrum divides by 2*pi*f. Band RMS values use the window's equivalent noise
bandwidth, so the RMS of a sine inside a band is about A / sqrt(2).
"""

from __future__ import annotations

import cmath
import math
from typing import Any, Sequence

AXES = ("x", "y", "z")
STANDARD_GRAVITY_MM_S2 = 9806.65
# Lowest frequency used for peaks and velocity: the sensor is specified from 5 Hz and
# the first bins are dominated by window leakage of the removed mean.
MIN_PEAK_HZ = 2.0
PEAK_COUNT = 3
# Peaks closer than this many bins to a stronger one belong to the same line.
PEAK_SEPARATION_BINS = 3
# Velocity RMS band per ISO 10816/20816 starts at 10 Hz; the top is the Nyquist frequency.
VELOCITY_RMS_MIN_HZ = 10.0
# Roll orders reported when the roll rotation frequency is known: (field name, multiple of 1x).
ORDERS = (("0_5x", 0.5), ("1x", 1.0), ("2x", 2.0), ("3x", 3.0))
# Search window around k * 1x: the line speed is an estimate (slip, diameter wear), so the
# strongest line within +-3 % (at least 1.5 bins) is taken.
ORDER_TOLERANCE = 0.03
ORDER_MIN_BINS = 1.5
# InfluxDB fields are numbers, so the roll frequency source is stored as a code.
ROLL_SOURCES = {"none": 0, "manual": 1, "auto": 2}
# Fixed bands in Hz (the last one is closed at the Nyquist frequency).
BANDS_HZ = ((2.0, 10.0), (10.0, 25.0), (25.0, 50.0), (50.0, 100.0), (100.0, None))


def band_key(low: float, high: float | None) -> str:
    return f"{low:g}_{high:g}" if high is not None else f"{low:g}_nyq"


def fft(values: Sequence[complex]) -> list[complex]:
    """Iterative radix-2 FFT; the length must be a power of two."""
    n = len(values)
    if n == 0 or n & (n - 1):
        raise ValueError("FFT length must be a power of two")
    data = list(values)
    j = 0
    for i in range(1, n):
        bit = n >> 1
        while j & bit:
            j ^= bit
            bit >>= 1
        j |= bit
        if i < j:
            data[i], data[j] = data[j], data[i]
    size = 2
    while size <= n:
        step = cmath.exp(-2j * math.pi / size)
        half = size // 2
        for start in range(0, n, size):
            w = 1 + 0j
            for k in range(start, start + half):
                t = w * data[k + half]
                data[k + half] = data[k] - t
                data[k] += t
                w *= step
        size *= 2
    return data


def hann(n: int) -> list[float]:
    # Periodic Hann window (DFT-even), the usual choice for spectral analysis.
    return [0.5 - 0.5 * math.cos(2 * math.pi * i / n) for i in range(n)]


def amplitude_spectrum(samples: Sequence[float]) -> list[float]:
    """Single-sided amplitude spectrum (bins 0..n/2) of a mean-removed, Hann-windowed signal."""
    n = len(samples)
    mean = sum(samples) / n
    window = hann(n)
    coherent_gain = sum(window)
    spectrum = fft([(s - mean) * w for s, w in zip(samples, window)])
    amplitudes = [abs(spectrum[k]) * 2 / coherent_gain for k in range(n // 2 + 1)]
    amplitudes[0] /= 2
    amplitudes[-1] /= 2
    return amplitudes


def _noise_bandwidth_bins(n: int) -> float:
    window = hann(n)
    return n * sum(w * w for w in window) / sum(window) ** 2


def band_rms(amplitudes: Sequence[float], df: float, low: float, high: float, enbw_bins: float) -> float:
    total = sum(a * a / 2 for k, a in enumerate(amplitudes) if low <= k * df < high)
    return math.sqrt(total / enbw_bins)


def velocity_spectrum(amplitudes: Sequence[float], df: float) -> list[float]:
    """Velocity amplitude in mm/s from an acceleration amplitude spectrum in g."""
    return [0.0 if k == 0 else a * STANDARD_GRAVITY_MM_S2 / (2 * math.pi * k * df) for k, a in enumerate(amplitudes)]


def find_peaks(amplitudes: Sequence[float], df: float, count: int = PEAK_COUNT,
               min_hz: float = MIN_PEAK_HZ) -> list[tuple[float, float]]:
    """Strongest local maxima as (frequency Hz, amplitude), refined by parabolic interpolation."""
    candidates = [k for k in range(1, len(amplitudes) - 1)
                  if k * df >= min_hz and amplitudes[k] > 0
                  and amplitudes[k] >= amplitudes[k - 1] and amplitudes[k] > amplitudes[k + 1]]
    candidates.sort(key=lambda k: amplitudes[k], reverse=True)
    chosen: list[int] = []
    for k in candidates:
        if all(abs(k - c) > PEAK_SEPARATION_BINS for c in chosen):
            chosen.append(k)
        if len(chosen) == count:
            break
    peaks = []
    for k in chosen:
        left, centre, right = amplitudes[k - 1], amplitudes[k], amplitudes[k + 1]
        denominator = left - 2 * centre + right
        offset = 0.5 * (left - right) / denominator if denominator else 0.0
        peaks.append(((k + offset) * df, centre))
    return peaks


def order_amplitude(amplitudes: Sequence[float], df: float, frequency: float) -> tuple[float, float] | None:
    """(frequency, amplitude) of the strongest line near `frequency`, or None when the
    window falls outside the usable spectrum."""
    half = max(ORDER_TOLERANCE * frequency, ORDER_MIN_BINS * df)
    low, high = frequency - half, frequency + half
    bins = [k for k in range(1, len(amplitudes) - 1) if low <= k * df <= high]
    if not bins or low < df:
        return None
    k = max(bins, key=lambda b: amplitudes[b])
    left, centre, right = amplitudes[k - 1], amplitudes[k], amplitudes[k + 1]
    denominator = left - 2 * centre + right
    offset = 0.5 * (left - right) / denominator if denominator and centre >= left and centre >= right else 0.0
    return (k + offset) * df, centre


def analyze(samples: Sequence[Sequence[float]], sample_rate: float, **options: Any) -> dict[str, Any]:
    """Spectrum and summary of one capture of (x, y, z) samples in g."""
    return analyze_average([samples], sample_rate, **options)


# Automatic roll speed detection: harmonic comb search in the velocity spectrum of all axes.
COMB_WEIGHTS = (1.0, 0.7, 0.5)            # weights of 1x, 2x, 3x
COMB_STEP_FRACTION = 0.25                 # candidate step in bins
NOISE_WINDOW_HZ = 2.0                     # half width of the local noise-floor window
# Noise floor = this percentile of the window: lower than the median, so dense harmonic
# families (looseness at a slow roll) do not lift the floor onto their own lines.
NOISE_PERCENTILE = 0.25
MIN_COMB_SNR = 4.0                        # comb score / noise needed to accept a result
MIN_CONFIDENCE = 0.25                     # 1 - second best / best (separated candidates)
# The comb's strongest line must be a visible part of the spectrum, not a tiny spur next to
# a dominant foreign line (relative to the strongest line above MIN_PEAK_HZ).
MIN_COMB_RELATIVE = 0.03
# The 1x line itself must be present: a lone line (e.g. 30 Hz) would otherwise be read as the
# 2x of half its frequency.
MIN_FIRST_ORDER_RELATIVE = 0.15
CANDIDATE_SEPARATION_BINS = 3
HARMONIC_RATIOS = (1 / 3, 0.5, 2 / 3, 1.5, 2.0, 3.0)
HARMONIC_RATIO_TOLERANCE = 0.03


def _local_floor(values: Sequence[float], half_bins: int) -> list[float]:
    floor = []
    for k in range(len(values)):
        window = sorted(values[max(1, k - half_bins):k + half_bins + 1])
        floor.append(window[int(len(window) * NOISE_PERCENTILE)] if window else 0.0)
    return floor


def detect_roll_hz(acc_by_axis: dict[str, Sequence[float]], df: float, low_hz: float,
                   high_hz: float) -> dict[str, float] | None:
    """Estimate the roll rotation frequency within [low_hz, high_hz] from the spectrum.

    Velocity amplitudes of all axes are combined in power; each line's prominence over
    the local noise floor (25th percentile within +-2 Hz) is used, so broadband noise does not count. Every candidate
    f is scored by the weighted prominence at 1x, 2x and 3x; the best candidate is
    refined from the peak positions. Subharmonics (f/2) lose because their own 1x line
    is missing; double the frequency loses because the true 1x is not on its comb.
    Confidence compares the best comb with the best unrelated one (candidates at simple
    ratios of the best frequency belong to the same harmonic family and are ignored).
    Returns {"hz", "confidence", "snr"} or None when no clear comb is found.
    """
    n = min(len(a) for a in acc_by_axis.values())
    if n < 8 or low_hz <= 0 or high_hz <= low_hz:
        return None
    combined = [0.0] * n
    for acc in acc_by_axis.values():
        for k, v in enumerate(velocity_spectrum(acc, df)[:n]):
            combined[k] += v * v
    combined = [math.sqrt(v) for v in combined]
    floor = _local_floor(combined, max(2, int(NOISE_WINDOW_HZ / df)))
    prominence = [max(0.0, c - f) for c, f in zip(combined, floor)]

    def line(frequency: float) -> tuple[float, float, float]:
        """(prominence, refined frequency, bin) of the strongest bin within +-1 bin."""
        centre = frequency / df
        bins = [k for k in range(int(centre) - 1, int(centre) + 3) if 1 <= k < n - 1 and abs(k - centre) <= 1.0]
        if not bins:
            return 0.0, frequency, centre
        k = max(bins, key=lambda b: prominence[b])
        left, mid, right = combined[k - 1], combined[k], combined[k + 1]
        denominator = left - 2 * mid + right
        offset = 0.5 * (left - right) / denominator if denominator and mid >= left and mid >= right else 0.0
        return prominence[k], (k + offset) * df, k

    def score(frequency: float) -> float:
        return sum(w * line(frequency * order)[0] for order, w in enumerate(COMB_WEIGHTS, start=1)
                   if frequency * order < (n - 2) * df)

    step = df * COMB_STEP_FRACTION
    candidates = []
    f = max(low_hz, df * 2)
    while f <= high_hz:
        candidates.append((score(f), f))
        f += step
    if not candidates:
        return None
    candidates.sort(reverse=True)
    best_score, best = candidates[0]
    # A competitor is a different harmonic family: candidates at simple ratios of the best one
    # (f/2 with half orders from looseness, 2f, f/3, ...) describe the same comb.
    def related(frequency: float) -> bool:
        ratio = frequency / best
        return any(abs(ratio - q) < HARMONIC_RATIO_TOLERANCE * q for q in HARMONIC_RATIOS)

    second = next((sc for sc, fr in candidates[1:]
                   if abs(fr - best) > CANDIDATE_SEPARATION_BINS * df and not related(fr)), 0.0)
    # Signal to noise against the local floor at the comb lines: the velocity noise floor
    # rises towards low frequencies, so a global floor would favour noise near 1-2 Hz.
    local_noise = sum(w * floor[min(n - 1, round(best * order / df))]
                      for order, w in enumerate(COMB_WEIGHTS, start=1) if best * order < (n - 2) * df)
    snr = best_score / (local_noise or 1e-12)
    confidence = 1.0 - second / best_score if best_score > 0 else 0.0
    strongest = max((prominence[k] for k in range(n) if k * df >= MIN_PEAK_HZ), default=0.0)
    comb_lines = [line(best * order)[0] for order in range(1, len(COMB_WEIGHTS) + 1)]
    comb_line = max(comb_lines)
    if (snr < MIN_COMB_SNR or confidence < MIN_CONFIDENCE or comb_line < MIN_COMB_RELATIVE * strongest
            or comb_lines[0] < MIN_FIRST_ORDER_RELATIVE * comb_line):
        return None
    # Refine: prominence-weighted mean of (line frequency / order).
    estimates = []
    for order in range(1, len(COMB_WEIGHTS) + 1):
        weight, frequency, _bin = line(best * order)
        if weight > 0:
            estimates.append((weight, frequency / order))
    if estimates:
        best = sum(w * f for w, f in estimates) / sum(w for w, _ in estimates)
    return {"hz": best, "confidence": confidence, "snr": snr}


def velocity_min_for(roll_hz: float | None) -> float:
    """ISO 10816-3 lower limit: 2 Hz up to 600 rpm (and when the speed is unknown), else 10 Hz."""
    return 10.0 if roll_hz and roll_hz * 60.0 > 600.0 else 2.0


def analyze_average(captures: Sequence[Sequence[Sequence[float]]], sample_rate: float,
                    velocity_min_hz: float | None = VELOCITY_RMS_MIN_HZ, roll_hz: float | None = None,
                    roll_search_hz: tuple[float, float] | None = None) -> dict[str, Any]:
    """Spectrum and summary averaged over captures of equal length.

    Captures are not contiguous (the sensor pauses between them), so they are not
    joined: each one is transformed on its own and the amplitudes are averaged in
    power (RMS averaging). Resolution stays that of one capture; random noise and
    transients are smoothed while steady lines keep their amplitude.

    roll_hz is a known roll frequency; with roll_search_hz = (low, high) the frequency is
    detected from the spectrum instead and roll_hz is only the fallback. velocity_min_hz
    None selects the ISO limit from the roll speed (velocity_min_for).
    """
    if not captures:
        raise ValueError("at least one capture is required")
    n = len(captures[0])
    if n < 8 or n & (n - 1):
        raise ValueError("sample count must be a power of two >= 8")
    if any(len(capture) != n for capture in captures):
        raise ValueError("captures must have the same length")
    if sample_rate <= 0:
        raise ValueError("sample rate must be positive")
    df = sample_rate / n
    nyquist = sample_rate / 2
    enbw = _noise_bandwidth_bins(n)
    acc_by_axis: dict[str, list[float]] = {}
    for index, axis in enumerate(AXES):
        spectra = [amplitude_spectrum([sample[index] for sample in capture]) for capture in captures]
        acc_by_axis[axis] = [math.sqrt(sum(s[k] * s[k] for s in spectra) / len(spectra)) for k in range(len(spectra[0]))]
    detection = detect_roll_hz(acc_by_axis, df, *roll_search_hz) if roll_search_hz else None
    if detection:
        roll_hz, roll_source = detection["hz"], "auto"
    else:
        roll_source = "manual" if roll_hz else "none"
    if velocity_min_hz is None:
        velocity_min_hz = velocity_min_for(roll_hz)
    result: dict[str, Any] = {
        "sample_rate_hz": sample_rate, "samples": n, "duration_s": n / sample_rate, "averages": len(captures),
        "df_hz": df, "nyquist_hz": nyquist, "velocity_min_hz": velocity_min_hz, "roll_hz": roll_hz,
        "roll_source": roll_source, "roll_detection": detection, "axes": {},
    }
    summary: dict[str, float] = {"sample_rate_hz": round(sample_rate, 3), "averages": float(len(captures)),
                                 "velocity_rms_min_hz": float(velocity_min_hz),
                                 "roll_source": float(ROLL_SOURCES[roll_source])}
    if roll_hz:
        summary["roll_hz"] = round(roll_hz, 4)
    if detection:
        summary["roll_confidence"] = round(detection["confidence"], 3)
    for axis in AXES:
        acc = acc_by_axis[axis]
        vel = velocity_spectrum(acc, df)
        peaks = find_peaks(acc, df)
        bands = {band_key(low, high): band_rms(acc, df, low, high if high is not None else nyquist + df, enbw)
                 for low, high in BANDS_HZ if low < nyquist}
        velocity_rms = band_rms(vel, df, velocity_min_hz, nyquist + df, enbw)
        orders: dict[str, dict[str, float]] = {}
        for name, multiple in ORDERS if roll_hz else ():
            found = order_amplitude(acc, df, roll_hz * multiple)
            if found:
                frequency, amplitude = found
                orders[name] = {"hz": frequency, "g": amplitude,
                                "mm_s": amplitude * STANDARD_GRAVITY_MM_S2 / (2 * math.pi * frequency)}
        result["axes"][axis] = {"acc_g": acc, "velocity_mm_s": vel, "peaks": peaks, "bands_g": bands,
                                "velocity_rms_mm_s": velocity_rms, "orders": orders}
        for name, order in orders.items():
            summary[f"order_{name}_{axis}_hz"] = round(order["hz"], 3)
            summary[f"order_{name}_{axis}_g"] = round(order["g"], 6)
            summary[f"order_{name}_{axis}_mm_s"] = round(order["mm_s"], 4)
        for rank, (frequency, amplitude) in enumerate(peaks, start=1):
            summary[f"peak{rank}_{axis}_hz"] = round(frequency, 3)
            summary[f"peak{rank}_{axis}_g"] = round(amplitude, 6)
        for key, value in bands.items():
            summary[f"band_{axis}_{key}_g"] = round(value, 6)
        summary[f"velocity_rms_{axis}_mm_s"] = round(velocity_rms, 4)
    result["summary"] = summary
    return result
