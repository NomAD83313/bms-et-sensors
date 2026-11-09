import json
from datetime import datetime, timedelta, timezone
from typing import Any


CSV_DELIMITERS = {
    "comma": ",",
    ",": ",",
    "semicolon": ";",
    "semi": ";",
    ";": ";",
    "tab": "\t",
    "tsv": "\t",
    "\\t": "\t",
}


def resolve_csv_delimiter(args) -> str:
    value = str(args.get("csv_delimiter", args.get("delimiter", "comma")) or "comma").strip().lower()
    return CSV_DELIMITERS.get(value, ",")


def resolve_csv_decimal_separator(delimiter: str) -> str:
    return "," if delimiter == ";" else "."


def resolve_dashboard_request(
    *,
    args,
    default_range: str,
    allowed_ranges: dict[str, int],
    sample_presets: dict[str, dict[str, Any]],
    max_points_per_series: int,
    safe_range_fn,
    safe_sample_key_fn,
    safe_target_points_fn,
    parse_iso_ts_fn,
    window_for_target_points_fn,
    estimated_points_for_window_fn,
):
    view_mode = str(args.get("view", "all") or "all").strip().lower()
    range_key = safe_range_fn(args.get("range", default_range))
    sample_key = safe_sample_key_fn(args.get("sample"))
    target_points = safe_target_points_fn(args.get("target_points"))
    custom_from_raw = args.get("from")
    custom_to_raw = args.get("to")

    window_from_dt = None
    window_to_dt = None
    if range_key == "custom":
        dt_from = parse_iso_ts_fn(custom_from_raw)
        dt_to = parse_iso_ts_fn(custom_to_raw)
        if dt_from is None or dt_to is None:
            return None, ({"success": False, "error": "Invalid custom range. Use ISO datetime in from/to."}, 400)
        if dt_to <= dt_from:
            return None, ({"success": False, "error": "Invalid custom range: 'to' must be greater than 'from'."}, 400)
        span_sec = int((dt_to - dt_from).total_seconds())
        if span_sec > (31 * 24 * 3600):
            return None, ({"success": False, "error": "Custom range too large (max 31 days)."}, 400)
        start_expr = f"time(v: {json.dumps(dt_from.isoformat().replace('+00:00', 'Z'))})"
        stop_expr = f"time(v: {json.dumps(dt_to.isoformat().replace('+00:00', 'Z'))})"
        range_span_sec = span_sec
        window_from_dt = dt_from
        window_to_dt = dt_to
    else:
        now_dt = datetime.now(timezone.utc)
        fallback_range_sec = int(allowed_ranges.get(default_range, allowed_ranges["5m"]))
        from_dt = now_dt - timedelta(seconds=int(allowed_ranges.get(range_key, fallback_range_sec)))
        start_expr = f"-{range_key}"
        stop_expr = None
        range_span_sec = int(allowed_ranges.get(range_key, fallback_range_sec))
        window_from_dt = from_dt
        window_to_dt = now_dt

    preset = sample_presets.get(sample_key, sample_presets["auto"])
    if sample_key == "auto":
        window = window_for_target_points_fn(range_span_sec, target_points)
        est_points = estimated_points_for_window_fn(range_span_sec, window)
        sample_label = f"Auto (~{est_points} pts)"
    else:
        sample_sec = preset.get("sec")
        if isinstance(sample_sec, (int, float)) and sample_sec > 0:
            est_points = int(float(range_span_sec) / float(sample_sec))
            if est_points > max_points_per_series:
                return None, (
                    {
                        "success": False,
                        "error": (
                            f"Requested sampling is too dense for current range "
                            f"(~{est_points} points/series, max {max_points_per_series}). "
                            "Reduce range or choose lower sampling frequency."
                        ),
                    },
                    400,
                )
            window = str(preset.get("duration"))
            sample_label = str(preset.get("label") or sample_key)
        else:
            window = window_for_target_points_fn(range_span_sec, target_points)
            est_points = estimated_points_for_window_fn(range_span_sec, window)
            sample_label = f"Auto (~{est_points} pts)"

    return {
        "view_mode": view_mode,
        "range_key": range_key,
        "sample_key": sample_key,
        "target_points": target_points,
        "custom_from_raw": custom_from_raw,
        "custom_to_raw": custom_to_raw,
        "start_expr": start_expr,
        "stop_expr": stop_expr,
        "window_from_dt": window_from_dt,
        "window_to_dt": window_to_dt,
        "window": window,
        "sample_label": sample_label,
    }, None


def resolve_export_window(
    *,
    args,
    default_range: str,
    allowed_ranges: dict[str, int],
    safe_range_fn,
    parse_iso_ts_fn,
):
    range_key = safe_range_fn(args.get("range", default_range))
    custom_from_raw = args.get("from")
    custom_to_raw = args.get("to")

    if range_key == "custom":
        dt_from = parse_iso_ts_fn(custom_from_raw)
        dt_to = parse_iso_ts_fn(custom_to_raw)
        if dt_from is None or dt_to is None or dt_to <= dt_from:
            return None, None, None
        span_sec = int((dt_to - dt_from).total_seconds())
        if span_sec > (31 * 24 * 3600):
            return None, None, None
        start_expr = f"time(v: {json.dumps(dt_from.isoformat().replace('+00:00', 'Z'))})"
        stop_expr = f"time(v: {json.dumps(dt_to.isoformat().replace('+00:00', 'Z'))})"
        return start_expr, stop_expr, span_sec

    start_expr = f"-{range_key}"
    stop_expr = None
    fallback_range_sec = int(allowed_ranges.get(default_range, allowed_ranges["5m"]))
    range_span_sec = int(allowed_ranges.get(range_key, fallback_range_sec))
    return start_expr, stop_expr, range_span_sec


def resolve_export_sampling(
    *,
    args,
    range_span_sec: int,
    sample_presets: dict[str, dict[str, Any]],
    max_points_per_series: int,
    safe_sample_key_fn,
    safe_target_points_fn,
    window_for_target_points_fn,
):
    export_mode = str(args.get("export_mode", "view")).strip().lower()
    if export_mode in {"raw", "raw_period", "raw_for_period"}:
        return "__raw__"
    if export_mode in {"custom", "custom_rate", "hz", "rate"}:
        try:
            export_hz = float(str(args.get("export_hz", "")).strip())
        except Exception:
            return None
        if export_hz <= 0:
            return None
        est_points = int(float(range_span_sec) * float(export_hz))
        if est_points > max_points_per_series:
            return None
        window_ms = max(1, int(round(1000.0 / export_hz)))
        return f"{window_ms}ms"

    sample_key = safe_sample_key_fn(args.get("sample"))
    target_points = safe_target_points_fn(args.get("target_points"))
    preset = sample_presets.get(sample_key, sample_presets["auto"])

    if sample_key == "auto":
        return window_for_target_points_fn(range_span_sec, target_points)

    sample_sec = preset.get("sec")
    if isinstance(sample_sec, (int, float)) and sample_sec > 0:
        est_points = int(float(range_span_sec) / float(sample_sec))
        if est_points > max_points_per_series:
            return None
        return str(preset.get("duration"))

    return window_for_target_points_fn(range_span_sec, target_points)


def resolve_export_request(
    *,
    args,
    default_range: str,
    allowed_ranges: dict[str, int],
    sample_presets: dict[str, dict[str, Any]],
    max_points_per_series: int,
    safe_range_fn,
    safe_sample_key_fn,
    safe_target_points_fn,
    parse_iso_ts_fn,
    window_for_target_points_fn,
):
    range_key = safe_range_fn(args.get("range", default_range))
    sample_key = safe_sample_key_fn(args.get("sample"))
    csv_delimiter = resolve_csv_delimiter(args)
    csv_decimal_separator = resolve_csv_decimal_separator(csv_delimiter)
    start_expr, stop_expr, range_span_sec = resolve_export_window(
        args=args,
        default_range=default_range,
        allowed_ranges=allowed_ranges,
        safe_range_fn=safe_range_fn,
        parse_iso_ts_fn=parse_iso_ts_fn,
    )
    if start_expr is None or range_span_sec is None:
        return None, ({"success": False, "error": "Invalid export range."}, 400)

    window = resolve_export_sampling(
        args=args,
        range_span_sec=range_span_sec,
        sample_presets=sample_presets,
        max_points_per_series=max_points_per_series,
        safe_sample_key_fn=safe_sample_key_fn,
        safe_target_points_fn=safe_target_points_fn,
        window_for_target_points_fn=window_for_target_points_fn,
    )
    if not window:
        return None, ({"success": False, "error": "Requested sampling is too dense for current range."}, 400)

    return {
        "range_key": range_key,
        "sample_key": sample_key,
        "start_expr": start_expr,
        "stop_expr": stop_expr,
        "range_span_sec": range_span_sec,
        "window": window,
        "raw_mode": window == "__raw__",
        "csv_delimiter": csv_delimiter,
        "csv_decimal_separator": csv_decimal_separator,
    }, None
