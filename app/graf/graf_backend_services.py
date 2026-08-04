import json
import os
import re
from datetime import timedelta
from datetime import timezone
from typing import Any

from influxdb_client import InfluxDBClient

from graf_csv_helpers import build_csv_content, make_csv_filename, make_csv_response
from graf_query_builders import (
    almemo_flux,
    matter_flux,
    matter_battery_flux,
    messkluppe_flux,
    mscl_flux,
    redlab_flux,
    redlab_flux_raw,
    split_env_list,
    tail_flux,
    pyrometers_flux,
)
from graf_series_helpers import (
    normalize_mscl_display_series,
    series_median_interval_ms,
)


PYROMETERS_REGISTRY_PATH = os.getenv("PYROMETERS_REGISTRY", "/runtime/pyrometers-devices.json")
_TIME_EXPR_RE = re.compile(r'time\(v:\s*"([^"]+)"\s*\)')
_CADENCE_LOOKBACK = timedelta(minutes=15)


def _load_pyrometer_serials(registry_path: str = PYROMETERS_REGISTRY_PATH) -> dict[str, str]:
    try:
        with open(registry_path, encoding="utf-8") as handle:
            entries = json.load(handle)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    if not isinstance(entries, list):
        return {}
    serials: dict[str, str] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        source = str(entry.get("id") or "").strip()
        serial = str(entry.get("serial") or "").strip()
        if source and serial:
            serials[source] = serial
    return serials


def _annotate_pyrometer_serials(series_list: list[dict[str, Any]], serials_by_source: dict[str, str]) -> list[dict[str, Any]]:
    if not serials_by_source:
        return series_list
    out: list[dict[str, Any]] = []
    for series in series_list:
        name = str(series.get("name") or "")
        parts = [part.strip() for part in name.split("|") if part.strip()]
        tags: dict[str, str] = {}
        for part in parts:
            key, sep, value = part.partition("=")
            if sep:
                tags[key.strip()] = value.strip()
        source = tags.get("source", "")
        serial = serials_by_source.get(source, "")
        if not serial or tags.get("serial"):
            out.append(series)
            continue
        insert_at = len(parts)
        for idx, part in enumerate(parts):
            if part.startswith("_field="):
                insert_at = idx
                break
        next_parts = [*parts[:insert_at], f"serial={serial}", *parts[insert_at:]]
        next_series = dict(series)
        next_series["name"] = " | ".join(next_parts)
        out.append(next_series)
    return out


def _format_time_expr(dt_obj) -> str:
    return f'time(v: {json.dumps(dt_obj.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"))})'


def _parse_time_expr(expr: str | None, parse_iso_ts_fn) -> Any | None:
    text = str(expr or "").strip()
    if not text:
        return None
    match = _TIME_EXPR_RE.fullmatch(text)
    if not match:
        return None
    return parse_iso_ts_fn(match.group(1))


def _cadence_range_bounds(
    start_expr: str,
    stop_expr: str | None,
    parse_iso_ts_fn,
    *,
    lookback: timedelta = _CADENCE_LOOKBACK,
) -> tuple[str, str | None]:
    if stop_expr is None:
        return f"-{int(max(1, lookback.total_seconds()))}s", None

    stop_dt = _parse_time_expr(stop_expr, parse_iso_ts_fn)
    if stop_dt is None:
        return start_expr, stop_expr

    bounded_start_dt = stop_dt - lookback
    start_dt = _parse_time_expr(start_expr, parse_iso_ts_fn)
    if start_dt is not None and start_dt > bounded_start_dt:
        bounded_start_dt = start_dt

    return _format_time_expr(bounded_start_dt), _format_time_expr(stop_dt)


def build_backend_services(
    *,
    influx_url: str,
    influx_token: str,
    influx_org: str,
    influx_bucket: str,
    mscl_measurement: str,
    mscl_source: str,
    mscl_source_extra: str,
    mscl_channel: str,
    redlab_measurement: str,
    almemo_measurement: str,
    pyrometers_measurement: str,
    messkluppe_measurement: str,
    matter_measurement: str,
    parse_iso_ts_fn,
):
    mscl_sources = split_env_list(",".join([mscl_source, mscl_source_extra]))

    def to_iso_z(dt_obj) -> str:
        try:
            return dt_obj.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        except Exception:
            try:
                return dt_obj.isoformat().replace("+00:00", "Z")
            except Exception:
                return ""

    def new_client() -> InfluxDBClient:
        return InfluxDBClient(url=influx_url, token=influx_token, org=influx_org)

    def query_series(flux_query: str, key_fields: list[str]) -> list[dict[str, Any]]:
        if not all([influx_token, influx_org, influx_bucket]):
            return []

        out: dict[str, dict[str, Any]] = {}
        try:
            with new_client() as client:
                tables = client.query_api().query(flux_query, org=influx_org)
                for table in tables:
                    for rec in table.records:
                        values = rec.values or {}
                        label_parts = []
                        for field in key_fields:
                            val = values.get(field)
                            if val is not None and str(val) != "":
                                label_parts.append(f"{field}={val}")
                        if not label_parts:
                            label_parts.append(f"field={rec.get_field()}")
                        key = " | ".join(label_parts)

                        series = out.setdefault(
                            key,
                            {
                                "name": key,
                                "points": [],
                            },
                        )
                        time_obj = rec.get_time()
                        sort_ns = None
                        try:
                            sort_ns = int(time_obj.timestamp() * 1_000_000_000)
                        except Exception:
                            sort_ns = None
                        series["points"].append(
                            {
                                "_sort_ns": sort_ns,
                                "t": to_iso_z(time_obj),
                                "v": rec.get_value(),
                            }
                        )
        except Exception:
            return []

        for series in out.values():
            points = list(series.get("points") or [])
            points.sort(
                key=lambda point: (
                    point.get("_sort_ns") is None,
                    point.get("_sort_ns") if point.get("_sort_ns") is not None else 0,
                    point.get("t", ""),
                )
            )
            for point in points:
                point.pop("_sort_ns", None)
            series["points"] = points
        return list(out.values())

    def mscl_query(start_expr: str, stop_expr: str | None, window: str | None = None) -> str:
        return mscl_flux(
            bucket=influx_bucket,
            measurement=mscl_measurement,
            channel=mscl_channel,
            source_values=mscl_sources,
            start_expr=start_expr,
            stop_expr=stop_expr,
            window=window,
        )

    def redlab_query(start_expr: str, stop_expr: str | None, window: str) -> str:
        if window == "__raw__":
            return redlab_flux_raw(
                bucket=influx_bucket,
                measurement=redlab_measurement,
                start_expr=start_expr,
                stop_expr=stop_expr,
            )
        return redlab_flux(
            bucket=influx_bucket,
            measurement=redlab_measurement,
            start_expr=start_expr,
            stop_expr=stop_expr,
            window=window,
        )

    def almemo_query(start_expr: str, stop_expr: str | None, window: str) -> str:
        return almemo_flux(
            bucket=influx_bucket,
            measurement=almemo_measurement,
            start_expr=start_expr,
            stop_expr=stop_expr,
            window=window,
        )

    def pyrometers_query(start_expr: str, stop_expr: str | None, window: str) -> str:
        return pyrometers_flux(
            bucket=influx_bucket,
            measurement=pyrometers_measurement,
            start_expr=start_expr,
            stop_expr=stop_expr,
            window=window,
        )

    def messkluppe_query(start_expr: str, stop_expr: str | None, window: str, fields: list[str] | tuple[str, ...] | None = None) -> str:
        return messkluppe_flux(
            bucket=influx_bucket,
            measurement=messkluppe_measurement,
            start_expr=start_expr,
            stop_expr=stop_expr,
            window=window,
            fields=fields,
        )

    def matter_query(
        start_expr: str,
        stop_expr: str | None,
        window: str,
        cluster_id: str = "1026",
        attribute_id: str | None = None,
        scale: float | None = None,
    ) -> str:
        value_scale = scale
        if value_scale is None:
            value_scale = 10.0 if cluster_id == "1027" and attribute_id == "16" else 100.0
        return matter_flux(
            bucket=influx_bucket,
            measurement=matter_measurement,
            start_expr=start_expr,
            stop_expr=stop_expr,
            window=window,
            cluster_id=cluster_id,
            attribute_id=attribute_id,
            scale=value_scale,
        )

    def matter_battery_query(start_expr: str, stop_expr: str | None, window: str) -> str:
        return matter_battery_flux(
            bucket=influx_bucket,
            measurement=matter_measurement,
            start_expr=start_expr,
            stop_expr=stop_expr,
            window=window,
        )

    def sampled_mscl_series(start_expr: str, stop_expr: str | None, window: str, raw_mode: bool = False) -> list[dict[str, Any]]:
        query_window = "__raw__" if raw_mode else window
        raw = query_series(mscl_query(start_expr, stop_expr, query_window), ["_measurement", "device", "source", "node_id", "channel"])
        normalized = normalize_mscl_display_series(raw, parse_iso_ts_fn)
        return normalized

    def load_mscl_series(start_expr: str, stop_expr: str | None, window: str, raw_mode: bool) -> list[dict[str, Any]]:
        return sampled_mscl_series(start_expr, stop_expr, window, raw_mode=raw_mode)

    def _parse_series_tags(name: str) -> dict[str, str]:
        tags: dict[str, str] = {}
        for part in str(name or "").split("|"):
            item = part.strip()
            if "=" not in item:
                continue
            key, value = item.split("=", 1)
            key = key.strip()
            value = value.strip()
            if key:
                tags[key] = value
        return tags

    def _dedupe_redlab_series(series_list: list[dict[str, Any]]) -> list[dict[str, Any]]:
        grouped: dict[str, dict[str, Any]] = {}
        for series in series_list:
            tags = _parse_series_tags(str(series.get("name") or ""))
            device = str(tags.get("device") or "")
            channel = str(tags.get("channel") or "")
            if not device or not channel:
                key = str(series.get("name") or "")
            else:
                key = f"{device}|{channel}"

            entry = grouped.setdefault(
                key,
                {
                    "preferred_name": str(series.get("name") or ""),
                    "has_named": False,
                    "points": [],
                },
            )

            channel_name = str(tags.get("channel_name") or "").strip()
            if channel_name:
                entry["has_named"] = True
                entry["preferred_name"] = str(series.get("name") or "")

            entry["points"].extend(list(series.get("points") or []))

        out: list[dict[str, Any]] = []
        for entry in grouped.values():
            points = list(entry.get("points") or [])
            points.sort(key=lambda p: str(p.get("t") or ""))
            dedup_by_t: dict[str, dict[str, Any]] = {}
            for p in points:
                t = str(p.get("t") or "")
                if not t:
                    continue
                dedup_by_t[t] = p
            merged_points = [dedup_by_t[t] for t in sorted(dedup_by_t)]
            out.append({"name": str(entry.get("preferred_name") or ""), "points": merged_points})
        out.sort(key=lambda s: str(s.get("name") or ""))
        return out

    def load_redlab_series(start_expr: str, stop_expr: str | None, window: str, raw_mode: bool) -> list[dict[str, Any]]:
        query = redlab_query(start_expr, stop_expr, "__raw__" if raw_mode else window)
        series = query_series(query, ["device", "channel", "channel_name"])
        return _dedupe_redlab_series(series)

    def load_almemo_series(start_expr: str, stop_expr: str | None, window: str, raw_mode: bool) -> list[dict[str, Any]]:
        del raw_mode
        return query_series(almemo_query(start_expr, stop_expr, window), ["device", "mode", "channel", "unit", "sensor"])

    def load_pyrometers_series(start_expr: str, stop_expr: str | None, window: str, raw_mode: bool) -> list[dict[str, Any]]:
        del raw_mode
        series = query_series(pyrometers_query(start_expr, stop_expr, window), ["source", "device", "_field"])
        return _annotate_pyrometer_serials(series, _load_pyrometer_serials())

    def load_messkluppe_series(start_expr: str, stop_expr: str | None, window: str, raw_mode: bool) -> list[dict[str, Any]]:
        query_window = "__raw__" if raw_mode else window
        return query_series(
            messkluppe_query(start_expr, stop_expr, query_window, ("force_x_raw", "force_y_raw", "force_z_raw")),
            ["source", "clip_id", "file_id", "_field"],
        )

    def load_messkluppe_orientation_series(start_expr: str, stop_expr: str | None, window: str, raw_mode: bool) -> list[dict[str, Any]]:
        query_window = "__raw__" if raw_mode else window
        return query_series(
            messkluppe_query(start_expr, stop_expr, query_window, ("yaw_deg",)),
            ["source", "clip_id", "file_id", "_field"],
        )

    def load_messkluppe_battery_series(start_expr: str, stop_expr: str | None, window: str, raw_mode: bool) -> list[dict[str, Any]]:
        query_window = "__raw__" if raw_mode else window
        return query_series(
            messkluppe_query(start_expr, stop_expr, query_window, ("battery_raw",)),
            ["source", "clip_id", "file_id", "_field"],
        )

    def load_messkluppe_temperature_series(start_expr: str, stop_expr: str | None, window: str, raw_mode: bool) -> list[dict[str, Any]]:
        query_window = "__raw__" if raw_mode else window
        return query_series(
            messkluppe_query(start_expr, stop_expr, query_window, ("imu_temperature_c", "clip_temperature_raw")),
            ["source", "clip_id", "file_id", "_field"],
        )

    def load_matter_series(start_expr: str, stop_expr: str | None, window: str, raw_mode: bool) -> list[dict[str, Any]]:
        del raw_mode
        return query_series(matter_query(start_expr, stop_expr, window), ["source", "node_id", "endpoint_id", "cluster_id"])

    def load_matter_humidity_series(start_expr: str, stop_expr: str | None, window: str, raw_mode: bool) -> list[dict[str, Any]]:
        del raw_mode
        return query_series(matter_query(start_expr, stop_expr, window, "1029"), ["source", "node_id", "endpoint_id", "cluster_id"])

    def load_matter_pressure_series(start_expr: str, stop_expr: str | None, window: str, raw_mode: bool) -> list[dict[str, Any]]:
        del raw_mode
        return query_series(
            matter_query(start_expr, stop_expr, window, "1027", "16"),
            ["source", "node_id", "endpoint_id", "cluster_id", "attribute_id"],
        )

    def load_matter_pm_series(start_expr: str, stop_expr: str | None, window: str, raw_mode: bool) -> list[dict[str, Any]]:
        del raw_mode
        series: list[dict[str, Any]] = []
        for cluster_id in ("1068", "1066", "1069"):
            series.extend(
                query_series(
                    matter_query(start_expr, stop_expr, window, cluster_id, "0", scale=1.0),
                    ["source", "node_id", "endpoint_id", "cluster_id", "attribute_id"],
                )
            )
        return series

    def load_matter_environment_series(start_expr: str, stop_expr: str | None, window: str, raw_mode: bool) -> list[dict[str, Any]]:
        series: list[dict[str, Any]] = []
        series.extend(load_matter_series(start_expr, stop_expr, window, raw_mode))
        series.extend(load_matter_humidity_series(start_expr, stop_expr, window, raw_mode))
        series.extend(load_matter_pressure_series(start_expr, stop_expr, window, raw_mode))
        series.extend(load_matter_pm_series(start_expr, stop_expr, window, raw_mode))
        return series

    def load_matter_battery_series(start_expr: str, stop_expr: str | None, window: str, raw_mode: bool) -> list[dict[str, Any]]:
        del raw_mode
        return query_series(
            matter_battery_query(start_expr, stop_expr, window),
            ["source", "node_id", "endpoint_id", "cluster_id", "attribute_id"],
        )

    def panel_raw_cadence_ms(panel_key: str, start_expr: str, stop_expr: str | None) -> float | None:
        cadence_start_expr, cadence_stop_expr = _cadence_range_bounds(start_expr, stop_expr, parse_iso_ts_fn)
        tail_n = 12
        if panel_key == "mscl_temperature":
            series = query_series(
                tail_flux(mscl_query(cadence_start_expr, cadence_stop_expr), tail_n),
                ["_measurement", "device", "source", "node_id", "channel"],
            )
            series = normalize_mscl_display_series(series, parse_iso_ts_fn)
            return series_median_interval_ms(series, parse_iso_ts_fn)
        if panel_key == "redlab_temperature":
            series = query_series(
                tail_flux(redlab_query(cadence_start_expr, cadence_stop_expr, "__raw__"), tail_n),
                ["device", "channel", "field"],
            )
            return series_median_interval_ms(series, parse_iso_ts_fn)
        if panel_key == "almemo_live":
            series = query_series(
                tail_flux(almemo_query(cadence_start_expr, cadence_stop_expr, "__raw__"), tail_n),
                ["device", "mode", "channel", "unit", "sensor"],
            )
            return series_median_interval_ms(series, parse_iso_ts_fn)
        if panel_key == "pyrometers_temperature":
            series = query_series(
                tail_flux(pyrometers_query(cadence_start_expr, cadence_stop_expr, "__raw__"), tail_n),
                ["source", "device", "_field"],
            )
            return series_median_interval_ms(series, parse_iso_ts_fn)
        if panel_key == "messkluppe_force":
            series = query_series(
                tail_flux(
                    messkluppe_query(
                        cadence_start_expr,
                        cadence_stop_expr,
                        "__raw__",
                        ("force_x_raw", "force_y_raw", "force_z_raw"),
                    ),
                    tail_n,
                ),
                ["source", "clip_id", "file_id", "_field"],
            )
            return series_median_interval_ms(series, parse_iso_ts_fn)
        if panel_key == "messkluppe_orientation":
            series = query_series(
                tail_flux(messkluppe_query(cadence_start_expr, cadence_stop_expr, "__raw__", ("yaw_deg",)), tail_n),
                ["source", "clip_id", "file_id", "_field"],
            )
            return series_median_interval_ms(series, parse_iso_ts_fn)
        if panel_key == "messkluppe_battery":
            series = query_series(
                tail_flux(messkluppe_query(start_expr, stop_expr, "__raw__", ("battery_raw",)), tail_n),
                ["source", "clip_id", "file_id", "_field"],
            )
            return series_median_interval_ms(series, parse_iso_ts_fn)
        if panel_key == "messkluppe_temperatures":
            series = query_series(
                tail_flux(messkluppe_query(start_expr, stop_expr, "__raw__", ("imu_temperature_c", "clip_temperature_raw")), tail_n),
                ["source", "clip_id", "file_id", "_field"],
            )
            return series_median_interval_ms(series, parse_iso_ts_fn)
        if panel_key == "matter_temperature":
            series = query_series(
                tail_flux(matter_query(cadence_start_expr, cadence_stop_expr, "__raw__"), tail_n),
                ["source", "node_id", "endpoint_id", "cluster_id"],
            )
            return series_median_interval_ms(series, parse_iso_ts_fn)
        if panel_key == "matter_humidity":
            series = query_series(
                tail_flux(matter_query(cadence_start_expr, cadence_stop_expr, "__raw__", "1029"), tail_n),
                ["source", "node_id", "endpoint_id", "cluster_id"],
            )
            return series_median_interval_ms(series, parse_iso_ts_fn)
        if panel_key == "matter_pressure":
            series = query_series(
                tail_flux(matter_query(cadence_start_expr, cadence_stop_expr, "__raw__", "1027", "16"), tail_n),
                ["source", "node_id", "endpoint_id", "cluster_id", "attribute_id"],
            )
            return series_median_interval_ms(series, parse_iso_ts_fn)
        if panel_key == "matter_pm":
            series: list[dict[str, Any]] = []
            for cluster_id in ("1068", "1066", "1069"):
                series.extend(
                    query_series(
                        tail_flux(matter_query(cadence_start_expr, cadence_stop_expr, "__raw__", cluster_id, "0", scale=1.0), tail_n),
                        ["source", "node_id", "endpoint_id", "cluster_id", "attribute_id"],
                    )
                )
            return series_median_interval_ms(series, parse_iso_ts_fn)
        if panel_key == "matter_battery":
            series = query_series(
                tail_flux(matter_battery_query(cadence_start_expr, cadence_stop_expr, "__raw__"), tail_n),
                ["source", "node_id", "endpoint_id", "cluster_id", "attribute_id"],
            )
            return series_median_interval_ms(series, parse_iso_ts_fn)
        return None

    def csv_export_response(
        *,
        by_ts: dict[str, dict[str, Any]],
        cols: set[str],
        precision: int,
        filename_prefix: str,
        sample_key: str,
        range_key: str,
        column_prefix: str = "",
        delimiter: str = ",",
        decimal_separator: str = ".",
    ):
        ordered_cols = [f"{column_prefix}{col}" for col in sorted(cols)]
        csv_content = build_csv_content(
            by_ts,
            ordered_cols,
            parse_iso_ts_fn=parse_iso_ts_fn,
            precision=precision,
            column_prefix=column_prefix,
            delimiter=delimiter,
            decimal_separator=decimal_separator,
        )
        filename = make_csv_filename(filename_prefix, sample_key, range_key)
        return make_csv_response(csv_content, filename)

    return {
        "to_iso_z_fn": to_iso_z,
        "load_mscl_series_fn": load_mscl_series,
        "load_redlab_series_fn": load_redlab_series,
        "load_almemo_series_fn": load_almemo_series,
        "load_pyrometers_series_fn": load_pyrometers_series,
        "load_messkluppe_series_fn": load_messkluppe_series,
        "load_messkluppe_orientation_series_fn": load_messkluppe_orientation_series,
        "load_messkluppe_battery_series_fn": load_messkluppe_battery_series,
        "load_messkluppe_temperature_series_fn": load_messkluppe_temperature_series,
        "load_matter_series_fn": load_matter_series,
        "load_matter_humidity_series_fn": load_matter_humidity_series,
        "load_matter_pressure_series_fn": load_matter_pressure_series,
        "load_matter_pm_series_fn": load_matter_pm_series,
        "load_matter_environment_series_fn": load_matter_environment_series,
        "load_matter_battery_series_fn": load_matter_battery_series,
        "panel_raw_cadence_ms_fn": panel_raw_cadence_ms,
        "csv_export_response_fn": csv_export_response,
    }
