import time
from datetime import datetime, timezone


def point_channel(dp):
    try:
        name = dp.channelName()
        if name:
            return str(name)
    except Exception:
        pass
    try:
        return f"ch{int(dp.channelId())}"
    except Exception:
        return "channel"


def point_value(dp):
    for getter in (
        lambda: dp.as_float(),
        lambda: dp.as_double(),
        lambda: dp.as_int32(),
        lambda: dp.as_uint32(),
        lambda: dp.as_int16(),
        lambda: dp.as_uint16(),
        lambda: dp.as_int8(),
        lambda: dp.as_uint8(),
        lambda: dp.value(),
    ):
        try:
            return float(getter())
        except Exception:
            continue
    return None


def point_time_ns(dp):
    """Best-effort datapoint timestamp in unix ns, with current-time fallback."""
    try:
        ts = dp.as_Timestamp()
        sec = int(ts.seconds())
        nsec = int(ts.nanoseconds())
        if sec > 0:
            if nsec < 0:
                nsec = 0
            elif nsec > 999_999_999:
                nsec = 999_999_999
            return sec * 1_000_000_000 + nsec
    except Exception:
        pass
    return time.time_ns()


def timestamp_to_ns(ts):
    try:
        sec = int(ts.seconds())
        nsec = int(ts.nanoseconds())
        if sec <= 0:
            return None
        if nsec < 0:
            nsec = 0
        elif nsec > 999_999_999:
            nsec = 999_999_999
        return (sec * 1_000_000_000) + nsec
    except Exception:
        return None


def ns_to_iso_utc(ts_ns):
    try:
        ts_ns = int(ts_ns)
        sec = ts_ns // 1_000_000_000
        nsec = ts_ns % 1_000_000_000
        dt = datetime.fromtimestamp(sec, tz=timezone.utc)
        return f"{dt.strftime('%Y-%m-%dT%H:%M:%S')}.{nsec:09d}Z"
    except Exception:
        return None


def logged_sweep_time_ns(sweep):
    try:
        return timestamp_to_ns(sweep.timestamp()) or time.time_ns()
    except Exception:
        return time.time_ns()


def coerce_logged_sweeps(batch):
    # MSCL Python bindings may return either LoggedDataSweep or LoggedDataSweeps.
    if batch is None:
        return []
    if hasattr(batch, "data") and callable(getattr(batch, "data", None)):
        return [batch]
    try:
        return list(batch)
    except Exception:
        return [batch]


def logged_sweep_rows(node_id, session_index, sample_rate_text, sweep):
    rows = []
    ts_ns = logged_sweep_time_ns(sweep)
    ts_iso = ns_to_iso_utc(ts_ns)
    try:
        tick = int(sweep.tick())
    except Exception:
        tick = None
    try:
        cal_applied = bool(sweep.calApplied())
    except Exception:
        cal_applied = None

    try:
        datapoints = sweep.data()
    except Exception:
        datapoints = []

    for dp in datapoints:
        value = point_value(dp)
        if value is None:
            continue
        channel = point_channel(dp)
        channel_id = None
        try:
            channel_id = int(dp.channelId())
        except Exception:
            pass
        rows.append(
            {
                "timestamp_utc": ts_iso,
                "timestamp_ns": int(ts_ns),
                "node_id": int(node_id),
                "session_index": session_index,
                "sample_rate": sample_rate_text,
                "channel": channel,
                "channel_id": channel_id,
                "value": float(value),
                "tick": tick,
                "cal_applied": cal_applied,
            }
        )
    return rows


__all__ = [
    "point_channel",
    "point_value",
    "point_time_ns",
    "timestamp_to_ns",
    "ns_to_iso_utc",
    "logged_sweep_time_ns",
    "coerce_logged_sweeps",
    "logged_sweep_rows",
]
