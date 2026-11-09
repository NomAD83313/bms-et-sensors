from datetime import datetime, timezone
from typing import Any


def duration_to_ns(window: str) -> int | None:
    text = str(window or "").strip().lower()
    if not text:
        return None
    try:
        if text.endswith("ms"):
            return int(float(text[:-2]) * 1_000_000)
        if text.endswith("s"):
            return int(float(text[:-1]) * 1_000_000_000)
        if text.endswith("m"):
            return int(float(text[:-1]) * 60 * 1_000_000_000)
        if text.endswith("h"):
            return int(float(text[:-1]) * 3600 * 1_000_000_000)
    except Exception:
        return None
    return None


def iso_z_to_ns(raw: str | None, parse_iso_ts_fn) -> int | None:
    dt = parse_iso_ts_fn(raw)
    if dt is None:
        return None
    try:
        return int(dt.timestamp() * 1_000_000_000)
    except Exception:
        return None


def ns_to_iso_z(ts_ns: int) -> str:
    try:
        dt = datetime.fromtimestamp(float(ts_ns) / 1_000_000_000.0, tz=timezone.utc)
        return dt.isoformat(timespec="microseconds").replace("+00:00", "Z")
    except Exception:
        return ""


def normalize_mscl_timestamps(series_list: list[dict[str, Any]], parse_iso_ts_fn) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    one_sec_ns = 1_000_000_000
    for series in series_list:
        rows: list[tuple[int, Any]] = []
        for point in (series.get("points") or []):
            ts_ns = iso_z_to_ns(point.get("t"), parse_iso_ts_fn)
            if ts_ns is None:
                continue
            rows.append((ts_ns, point.get("v")))
        rows.sort(key=lambda row: row[0])
        if not rows:
            out.append({"name": str(series.get("name") or ""), "points": []})
            continue

        grouped: dict[int, list[tuple[int, Any]]] = {}
        for ts_ns, value in rows:
            sec_key = ts_ns // one_sec_ns
            grouped.setdefault(sec_key, []).append((ts_ns, value))

        normalized_points: list[dict[str, Any]] = []
        for sec_key in sorted(grouped.keys()):
            group = grouped[sec_key]
            group_len = len(group)
            sec_base = sec_key * one_sec_ns
            for index, (_, value) in enumerate(group):
                shifted_ns = sec_base + int((index / group_len) * one_sec_ns)
                normalized_points.append({"t": ns_to_iso_z(shifted_ns), "v": value})
        out.append({"name": str(series.get("name") or ""), "points": normalized_points})
    return out


def is_node_export_series_name(name: str) -> bool:
    text = str(name or "").lower()
    return "source=mscl_node_export" in text


def normalize_mscl_display_series(series_list: list[dict[str, Any]], parse_iso_ts_fn) -> list[dict[str, Any]]:
    normalize_in: list[dict[str, Any]] = []
    passthrough_raw: list[dict[str, Any]] = []
    for series in series_list:
        if is_node_export_series_name(series.get("name", "")):
            passthrough_raw.append(series)
        else:
            normalize_in.append(series)
    return normalize_mscl_timestamps(normalize_in, parse_iso_ts_fn) + passthrough_raw


def resample_series(series_list: list[dict[str, Any]], window_ns: int, parse_iso_ts_fn) -> list[dict[str, Any]]:
    if window_ns <= 1:
        return series_list
    out: list[dict[str, Any]] = []
    for series in series_list:
        buckets: dict[int, list[float]] = {}
        for point in (series.get("points") or []):
            ts_ns = iso_z_to_ns(point.get("t"), parse_iso_ts_fn)
            try:
                value = float(point.get("v"))
            except Exception:
                continue
            if ts_ns is None:
                continue
            bucket = ts_ns // window_ns
            buckets.setdefault(bucket, []).append(value)

        points: list[dict[str, Any]] = []
        for bucket in sorted(buckets.keys()):
            values = buckets[bucket]
            if not values:
                continue
            ts_ns = bucket * window_ns
            points.append({"t": ns_to_iso_z(ts_ns), "v": sum(values) / float(len(values))})
        out.append({"name": str(series.get("name") or ""), "points": points})
    return out


def series_median_interval_ms(series_list: list[dict[str, Any]], parse_iso_ts_fn) -> float | None:
    medians_ms: list[float] = []
    for series in series_list:
        ts_values_ns: list[int] = []
        for point in (series.get("points") or []):
            ts_ns = iso_z_to_ns(point.get("t"), parse_iso_ts_fn)
            if ts_ns is not None:
                ts_values_ns.append(ts_ns)
        if len(ts_values_ns) < 2:
            continue
        ts_values_ns.sort()
        deltas_ms: list[float] = []
        prev_ts = ts_values_ns[0]
        for ts_ns in ts_values_ns[1:]:
            if ts_ns > prev_ts:
                deltas_ms.append(float(ts_ns - prev_ts) / 1_000_000.0)
            prev_ts = ts_ns
        if not deltas_ms:
            continue
        deltas_ms.sort()
        mid = len(deltas_ms) // 2
        if len(deltas_ms) % 2 == 1:
            medians_ms.append(deltas_ms[mid])
        else:
            medians_ms.append((deltas_ms[mid - 1] + deltas_ms[mid]) / 2.0)
    if not medians_ms:
        return None
    medians_ms.sort()
    mid = len(medians_ms) // 2
    if len(medians_ms) % 2 == 1:
        return medians_ms[mid]
    return (medians_ms[mid - 1] + medians_ms[mid]) / 2.0
