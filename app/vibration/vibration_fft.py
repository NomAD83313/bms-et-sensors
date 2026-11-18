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


def analyze(samples: Sequence[Sequence[float]], sample_rate: float) -> dict[str, Any]:
    """Spectrum and summary of one capture of (x, y, z) samples in g."""
    return analyze_average([samples], sample_rate)


def analyze_average(captures: Sequence[Sequence[Sequence[float]]], sample_rate: float) -> dict[str, Any]:
    """Spectrum and summary averaged over captures of equal length.

    Captures are not contiguous (the sensor pauses between them), so they are not
    joined: each one is transformed on its own and the amplitudes are averaged in
    power (RMS averaging). Resolution stays that of one capture; random noise and
    transients are smoothed while steady lines keep their amplitude.
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
    result: dict[str, Any] = {
        "sample_rate_hz": sample_rate, "samples": n, "duration_s": n / sample_rate, "averages": len(captures),
        "df_hz": df, "nyquist_hz": nyquist, "axes": {},
    }
    summary: dict[str, float] = {"sample_rate_hz": round(sample_rate, 3), "averages": float(len(captures))}
    for index, axis in enumerate(AXES):
        spectra = [amplitude_spectrum([sample[index] for sample in capture]) for capture in captures]
        acc = [math.sqrt(sum(s[k] * s[k] for s in spectra) / len(spectra)) for k in range(len(spectra[0]))]
        vel = velocity_spectrum(acc, df)
        peaks = find_peaks(acc, df)
        bands = {band_key(low, high): band_rms(acc, df, low, high if high is not None else nyquist + df, enbw)
                 for low, high in BANDS_HZ if low < nyquist}
        velocity_rms = band_rms(vel, df, VELOCITY_RMS_MIN_HZ, nyquist + df, enbw)
        result["axes"][axis] = {"acc_g": acc, "velocity_mm_s": vel, "peaks": peaks, "bands_g": bands,
                                "velocity_rms_mm_s": velocity_rms}
        for rank, (frequency, amplitude) in enumerate(peaks, start=1):
            summary[f"peak{rank}_{axis}_hz"] = round(frequency, 3)
            summary[f"peak{rank}_{axis}_g"] = round(amplitude, 6)
        for key, value in bands.items():
            summary[f"band_{axis}_{key}_g"] = round(value, 6)
        summary[f"velocity_rms_{axis}_mm_s"] = round(velocity_rms, 4)
    result["summary"] = summary
    return result
