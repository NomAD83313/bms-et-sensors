from datetime import datetime, timezone
from typing import Iterable, Optional, Tuple


def parse_iso_utc_to_ns(raw_value, name):
    s = str(raw_value or "").strip()
    if not s:
        raise ValueError(f"Missing {name}")
    s_norm = s.replace(" ", "T")
    if s_norm.endswith("Z"):
        s_norm = s_norm[:-1] + "+00:00"
    dt = datetime.fromisoformat(s_norm)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    dt_utc = dt.astimezone(timezone.utc)
    return int(dt_utc.timestamp() * 1_000_000_000)


def resolve_export_time_window(
    export_format: str,
    ui_window_from_ns: Optional[int],
    ui_window_to_ns: Optional[int],
    host_hours: Optional[float],
    now_ns: Optional[int] = None,
) -> Tuple[Optional[int], Optional[int], Optional[str]]:
    # Apply time window for API-only ingestion too (format=none), because UI
    # uses this mode for "ingest to Influx without file download".
    if export_format not in ("csv", "json", "none"):
        return None, None, None

    if ui_window_from_ns is not None and ui_window_to_ns is not None:
        return int(ui_window_from_ns), int(ui_window_to_ns), "ui"

    if host_hours is not None:
        ts_to = int(now_ns if now_ns is not None else datetime.now(tz=timezone.utc).timestamp() * 1_000_000_000)
        ts_from = ts_to - int(float(host_hours) * 3600.0 * 1_000_000_000)
        return ts_from, ts_to, "host_hours"

    return None, None, None


def filter_rows_by_host_window(
    rows: Iterable[dict],
    window_from_ns: int,
    window_to_ns: int,
    time_offset_ns: int = 0,
) -> list:
    out = []
    lo = int(window_from_ns)
    hi = int(window_to_ns)
    offset = int(time_offset_ns)
    for row in rows:
        ts_ns = row.get("timestamp_ns")
        if ts_ns is None:
            continue
        try:
            host_ts_ns = int(ts_ns) + offset
        except (TypeError, ValueError):
            continue
        if lo <= host_ts_ns <= hi:
            out.append(row)
    return out


__all__ = [
    "filter_rows_by_host_window",
    "parse_iso_utc_to_ns",
    "resolve_export_time_window",
]
