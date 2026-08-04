import csv
import io
import re
from datetime import datetime, timezone
from typing import Any, Callable

from flask import Response


def csv_timestamp_fields(raw_ts: str | None, parse_iso_ts_fn) -> tuple[str, str]:
    dt = parse_iso_ts_fn(raw_ts)
    if dt is None:
        return str(raw_ts or ""), ""
    ts_human = dt.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    try:
        ts_unix_ms = str(int(dt.timestamp() * 1000.0))
    except Exception:
        ts_unix_ms = ""
    return ts_human, ts_unix_ms


def append_series_rows(
    by_ts: dict[str, dict[str, Any]],
    cols: set[str],
    series_list: list[dict[str, Any]],
    column_name_fn: Callable[[str], str],
) -> None:
    for series in series_list:
        col = column_name_fn(str(series.get("name") or ""))
        cols.add(col)
        for point in (series.get("points") or []):
            ts_utc = str(point.get("t") or "")
            row = by_ts.setdefault(ts_utc, {})
            row[col] = point.get("v")


def build_csv_content(
    by_ts: dict[str, dict[str, Any]],
    ordered_cols: list[str],
    *,
    parse_iso_ts_fn,
    precision: int,
    column_prefix: str = "",
    delimiter: str = ",",
    decimal_separator: str = ".",
) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=delimiter, lineterminator="\r\n")
    writer.writerow(["timestamp_utc", "timestamp_unix_ms", *ordered_cols])
    for ts_utc in sorted(by_ts.keys()):
        row = by_ts[ts_utc]
        ts_human, ts_unix_ms = csv_timestamp_fields(ts_utc, parse_iso_ts_fn)
        values = []
        for col in ordered_cols:
            lookup_key = col
            if column_prefix and col.startswith(column_prefix):
                lookup_key = col[len(column_prefix):]
            raw_val = row.get(lookup_key)
            if raw_val is None:
                values.append("")
            else:
                try:
                    formatted = f"{float(raw_val):.{precision}f}"
                    if decimal_separator == ",":
                        formatted = formatted.replace(".", ",")
                    values.append(formatted)
                except Exception:
                    values.append("")
        writer.writerow([ts_human, ts_unix_ms, *values])
    return buf.getvalue()


def make_csv_filename(prefix: str, sample_key: str, range_key: str) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{prefix}_{sample_key}_{range_key}_{ts}.csv"


def make_csv_response(csv_content: str, filename: str) -> Response:
    if not csv_content.startswith("\ufeff"):
        csv_content = "\ufeff" + csv_content
    return Response(
        csv_content,
        mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


def mscl_csv_column_name(series_name: str, stream_source: str, export_source: str) -> str:
    source = ""
    node_id = ""
    channel = ""
    for part in str(series_name or "").split(" | "):
        part = part.strip()
        if part.startswith("source="):
            source = part.split("=", 1)[1]
        elif part.startswith("node_id="):
            node_id = part.split("=", 1)[1]
        elif part.startswith("channel="):
            channel = part.split("=", 1)[1]

    if source == stream_source:
        base = "mscl_stream"
    elif source == export_source:
        base = "mscl_export"
    else:
        base = f"mscl_{source or 'unknown'}"

    if channel and channel != "ch1":
        base = f"{base}_{channel}"
    if node_id and node_id != "16904":
        base = f"{base}_n{node_id}"
    return base


def almemo_csv_column_name(series_name: str) -> str:
    channel = ""
    sensor = ""
    mode = ""
    for part in str(series_name or "").split(" | "):
        part = part.strip()
        if part.startswith("channel="):
            channel = part.split("=", 1)[1]
        elif part.startswith("sensor="):
            sensor = part.split("=", 1)[1]
        elif part.startswith("mode="):
            mode = part.split("=", 1)[1]
    parts = ["almemo"]
    if channel:
        parts.append(_sanitize(channel))
    if sensor:
        parts.append(_sanitize(sensor))
    if mode:
        parts.append(_sanitize(mode))
    return "_".join(parts) if len(parts) > 1 else "almemo_unknown"


def pyrometers_csv_column_name(series_name: str) -> str:
    source = ""
    device = ""
    serial = ""
    field = ""
    for part in str(series_name or "").split(" | "):
        part = part.strip()
        if part.startswith("source="):
            source = part.split("=", 1)[1]
        elif part.startswith("device="):
            device = part.split("=", 1)[1]
        elif part.startswith("serial="):
            serial = part.split("=", 1)[1]
        elif part.startswith("_field="):
            field = part.split("=", 1)[1]
    field_names = {
        "object_temperature_c": "tobj",
        "sensor_head_temperature_c": "thead",
        "controller_box_temperature_c": "tbox",
    }
    field_name = field_names.get(field, field or "temperature")
    device_id = serial or source or device or "pyrometers"
    return f"{_sanitize(device_id)}_{_sanitize(field_name)}"


def messkluppe_csv_column_name(series_name: str) -> str:
    clip_id = ""
    file_id = ""
    field = ""
    for part in str(series_name or "").split(" | "):
        part = part.strip()
        if part.startswith("clip_id="):
            clip_id = part.split("=", 1)[1]
        elif part.startswith("file_id="):
            file_id = part.split("=", 1)[1]
        elif part.startswith("_field="):
            field = part.split("=", 1)[1]
    parts = ["messkluppe"]
    if clip_id:
        parts.append(f"clip_{_sanitize(clip_id)}")
    if file_id:
        parts.append(f"file_{_sanitize(file_id)}")
    parts.append(_sanitize(field or "force"))
    return "_".join(parts)


def matter_csv_column_name(series_name: str) -> str:
    node_id = ""
    endpoint_id = ""
    cluster_id = ""
    attribute_id = ""
    for part in str(series_name or "").split(" | "):
        part = part.strip()
        if part.startswith("node_id="):
            node_id = part.split("=", 1)[1]
        elif part.startswith("endpoint_id="):
            endpoint_id = part.split("=", 1)[1]
        elif part.startswith("cluster_id="):
            cluster_id = part.split("=", 1)[1]
        elif part.startswith("attribute_id="):
            attribute_id = part.split("=", 1)[1]
    parts = ["matter"]
    if node_id:
        parts.append(f"node_{_sanitize(node_id)}")
    if endpoint_id:
        parts.append(f"ep_{_sanitize(endpoint_id)}")
    if cluster_id == "1026":
        parts.append("temp")
    elif cluster_id == "1029":
        parts.append("humidity")
    elif cluster_id == "1027":
        parts.append("pressure_hpa")
    elif cluster_id == "1068":
        parts.append("pm1_ug_m3")
    elif cluster_id == "1066":
        parts.append("pm25_ug_m3")
    elif cluster_id == "1069":
        parts.append("pm10_ug_m3")
    elif cluster_id:
        parts.append(f"cluster_{_sanitize(cluster_id)}")
    return "_".join(parts) if len(parts) > 1 else "matter_unknown"


def redlab_csv_column_name(series_name: str, custom_names: dict[str, str] | None = None) -> str:
    device = ""
    channel = ""
    for part in str(series_name or "").split(" | "):
        part = part.strip()
        if part.startswith("device="):
            device = part.split("=", 1)[1]
        if part.startswith("channel="):
            channel = part.split("=", 1)[1]

    if custom_names:
        lookup_keys = []
        if device and channel:
            lookup_keys.append(f"{device}|{channel}")
        if channel:
            lookup_keys.append(channel)
        for key in lookup_keys:
            name = str(custom_names.get(key, "") or "").strip()
            if name:
                return _sanitize(name).lower()

    if device and channel:
        return f"{_sanitize(device)}_{_sanitize(channel)}"
    if channel:
        return _sanitize(channel)
    return "unknown"


def _sanitize(s: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "_", s).strip("_")
