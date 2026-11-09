from __future__ import annotations


def stream_is_stale(
    *,
    now_monotonic: float,
    stale_after_sec: float,
    last_valid_frame_monotonic: float | None,
    stream_opened_monotonic: float | None,
) -> bool:
    if stale_after_sec <= 0.0:
        return False
    reference = last_valid_frame_monotonic if last_valid_frame_monotonic is not None else stream_opened_monotonic
    return reference is not None and now_monotonic - reference >= stale_after_sec
