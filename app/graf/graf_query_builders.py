import json


def split_env_list(raw: str) -> list[str]:
    values: list[str] = []
    for token in str(raw or "").split(","):
        value = token.strip()
        if value and value not in values:
            values.append(value)
    return values


def range_line(start_expr: str, stop_expr: str | None = None) -> str:
    if stop_expr:
        return f"  |> range(start: {start_expr}, stop: {stop_expr})\n"
    return f"  |> range(start: {start_expr})\n"


def tail_flux(flux_query: str, n: int) -> str:
    limit_n = max(2, int(n))
    return f"{flux_query}\n  |> tail(n: {limit_n})"


def mscl_flux(
    *,
    bucket: str,
    measurement: str,
    channel: str,
    source_values: list[str],
    start_expr: str,
    stop_expr: str | None,
    window: str | None = None,
) -> str:
    bucket_q = json.dumps(bucket)
    measurement_q = json.dumps(measurement)
    channel_q = json.dumps(channel)
    if source_values:
        source_filter = " or ".join(f"r.source == {json.dumps(value)}" for value in source_values)
    else:
        source_filter = "true"

    query = (
        f"from(bucket: {bucket_q})\n"
        + range_line(start_expr, stop_expr)
        + f"  |> filter(fn: (r) => r._measurement == {measurement_q})\n"
        + '  |> filter(fn: (r) => r._field == "value")\n'
        + f"  |> filter(fn: (r) => {source_filter})\n"
        + f"  |> filter(fn: (r) => r.channel == {channel_q})\n"
    )
    if window and window != "__raw__":
        query += f"  |> aggregateWindow(every: {window}, fn: max, createEmpty: false)\n"
    query += '  |> keep(columns: ["_time", "_value", "_measurement", "device", "source", "channel", "node_id"])'
    return query


def redlab_flux(*, bucket: str, measurement: str, start_expr: str, stop_expr: str | None, window: str) -> str:
    bucket_q = json.dumps(bucket)
    measurement_q = json.dumps(measurement)
    query = (
        f"from(bucket: {bucket_q})\n"
        + range_line(start_expr, stop_expr)
        + f"  |> filter(fn: (r) => r._measurement == {measurement_q})\n"
        + '  |> filter(fn: (r) => r._field == "value")\n'
    )
    query += f"  |> aggregateWindow(every: {window}, fn: mean, createEmpty: false)\n"
    query += '  |> keep(columns: ["_time", "_value", "device", "channel", "channel_name"])'
    return query


def redlab_flux_raw(*, bucket: str, measurement: str, start_expr: str, stop_expr: str | None) -> str:
    bucket_q = json.dumps(bucket)
    measurement_q = json.dumps(measurement)
    query = (
        f"from(bucket: {bucket_q})\n"
        + range_line(start_expr, stop_expr)
        + f"  |> filter(fn: (r) => r._measurement == {measurement_q})\n"
        + '  |> filter(fn: (r) => r._field == "value")\n'
    )
    query += '  |> keep(columns: ["_time", "_value", "device", "channel", "channel_name"])'
    return query


def almemo_flux(*, bucket: str, measurement: str, start_expr: str, stop_expr: str | None, window: str) -> str:
    bucket_q = json.dumps(bucket)
    measurement_q = json.dumps(measurement)
    query = (
        f"from(bucket: {bucket_q})\n"
        + range_line(start_expr, stop_expr)
        + f"  |> filter(fn: (r) => r._measurement == {measurement_q})\n"
        + '  |> filter(fn: (r) => r._field == "value")\n'
    )
    if window and window != "__raw__":
        query += f"  |> aggregateWindow(every: {window}, fn: mean, createEmpty: false)\n"
    query += '  |> keep(columns: ["_time", "_value", "device", "mode", "channel", "unit", "sensor"])\n'
    return query


def pyrometers_flux(*, bucket: str, measurement: str, start_expr: str, stop_expr: str | None, window: str) -> str:
    bucket_q = json.dumps(bucket)
    measurement_q = json.dumps(measurement)
    query = (
        f"from(bucket: {bucket_q})\n"
        + range_line(start_expr, stop_expr)
        + f"  |> filter(fn: (r) => r._measurement == {measurement_q})\n"
        + '  |> filter(fn: (r) => r._field == "object_temperature_c")\n'
    )
    if window and window != "__raw__":
        query += f"  |> aggregateWindow(every: {window}, fn: mean, createEmpty: false)\n"
    query += '  |> keep(columns: ["_time", "_value", "_field", "source", "device", "serial"])\n'
    return query


def messkluppe_flux(
    *,
    bucket: str,
    measurement: str,
    start_expr: str,
    stop_expr: str | None,
    window: str,
    fields: list[str] | tuple[str, ...] | None = None,
) -> str:
    bucket_q = json.dumps(bucket)
    measurement_q = json.dumps(measurement)
    selected_fields = list(fields or ("force_x_raw", "force_y_raw", "force_z_raw"))
    field_filter = " or ".join(f"r._field == {json.dumps(field)}" for field in selected_fields)
    query = (
        f"from(bucket: {bucket_q})\n"
        + range_line(start_expr, stop_expr)
        + f"  |> filter(fn: (r) => r._measurement == {measurement_q})\n"
        + f"  |> filter(fn: (r) => {field_filter})\n"
    )
    if window and window != "__raw__":
        query += f"  |> aggregateWindow(every: {window}, fn: mean, createEmpty: false)\n"
    query += '  |> keep(columns: ["_time", "_value", "_field", "source", "clip_id", "file_id"])\n'
    return query


def matter_flux(
    *,
    bucket: str,
    measurement: str,
    start_expr: str,
    stop_expr: str | None,
    window: str,
    cluster_id: str = "1026",
    attribute_id: str | None = None,
    scale: float | None = None,
) -> str:
    bucket_q = json.dumps(bucket)
    measurement_q = json.dumps(measurement)
    cluster_id_q = json.dumps(str(cluster_id))
    attribute_id_q = json.dumps(str(attribute_id)) if attribute_id is not None else None
    if str(cluster_id) == "1027" and str(attribute_id) == "16":
        value_scale = 10.0
    elif str(cluster_id) == "1027":
        value_scale = 0.1
    else:
        value_scale = 100.0
    if scale is not None:
        value_scale = scale
    query = (
        f"from(bucket: {bucket_q})\n"
        + range_line(start_expr, stop_expr)
        + f"  |> filter(fn: (r) => r._measurement == {measurement_q})\n"
        + '  |> filter(fn: (r) => r.event_type == "attribute_updated" or r.event_type == "poll_attribute")\n'
        + f"  |> filter(fn: (r) => r.cluster_id == {cluster_id_q})\n"
        + '  |> filter(fn: (r) => r._field == "value")\n'
    )
    if attribute_id_q is not None:
        query += f"  |> filter(fn: (r) => r.attribute_id == {attribute_id_q})\n"
    if window and window != "__raw__":
        query += f"  |> aggregateWindow(every: {window}, fn: mean, createEmpty: false)\n"
    query += f"  |> map(fn: (r) => ({{ r with _value: r._value / {value_scale:.1f} }}))\n"
    query += '  |> keep(columns: ["_time", "_value", "source", "node_id", "endpoint_id", "cluster_id", "attribute_id"])\n'
    return query


def matter_battery_flux(*, bucket: str, measurement: str, start_expr: str, stop_expr: str | None, window: str) -> str:
    bucket_q = json.dumps(bucket)
    measurement_q = json.dumps(measurement)
    query = (
        f"from(bucket: {bucket_q})\n"
        + range_line(start_expr, stop_expr)
        + f"  |> filter(fn: (r) => r._measurement == {measurement_q})\n"
        + '  |> filter(fn: (r) => r.event_type == "attribute_updated" or r.event_type == "poll_attribute")\n'
        + '  |> filter(fn: (r) => r.cluster_id == "47")\n'
        + '  |> filter(fn: (r) => r.attribute_id == "12")\n'
        + '  |> filter(fn: (r) => r._field == "value")\n'
    )
    if window and window != "__raw__":
        query += f"  |> aggregateWindow(every: {window}, fn: mean, createEmpty: false)\n"
    query += '  |> map(fn: (r) => ({ r with _value: r._value / 2.0 }))\n'
    query += '  |> keep(columns: ["_time", "_value", "source", "node_id", "endpoint_id", "cluster_id", "attribute_id"])\n'
    return query
