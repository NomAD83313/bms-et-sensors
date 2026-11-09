import json

from influxdb_client import InfluxDBClient, Point  # type: ignore
from influxdb_client.client.write_api import SYNCHRONOUS  # type: ignore
from influxdb_client.domain.write_precision import WritePrecision  # type: ignore


def backfill_rows_to_influx_stream(
    node_id,
    rows,
    source_tag,
    influx_url,
    influx_token,
    influx_org,
    influx_bucket,
    measurement,
    export_batch_size,
    ns_to_iso_utc_fn,
):
    if not rows:
        return {"written": 0, "skipped_existing": 0}
    if not all([influx_token, influx_org, influx_bucket]):
        raise RuntimeError("Influx is not configured (missing token/org/bucket)")

    node_tag = str(int(node_id))
    source_tag = str(source_tag or "mscl_node_export")
    batch = []
    total_written = 0
    total_skipped_existing = 0
    point_key_counts = {}
    channel_ranges = {}
    raw_channel_ranges = {}
    candidates = []

    # Node-export backfill can involve expensive dedupe queries + large writes.
    # Increase client timeout beyond default 10s to avoid false failures.
    with InfluxDBClient(url=influx_url, token=influx_token, org=influx_org, timeout=120_000) as db_client:
        query_api = db_client.query_api()
        write_api = db_client.write_api(write_options=SYNCHRONOUS)

        for row in rows:
            channel = str(row.get("channel") or "").strip()
            if not channel:
                continue
            try:
                value = float(row.get("value"))
                raw_ts_ns = int(row.get("timestamp_ns"))
            except Exception:
                continue
            tick_raw = row.get("tick")
            tick_val = None
            try:
                if tick_raw is not None:
                    tick_val = int(tick_raw)
            except Exception:
                tick_val = None
            ts_ns = int(raw_ts_ns)
            if ts_ns <= 0:
                continue

            key = (node_tag, channel, ts_ns)
            dup_idx = point_key_counts.get(key, 0)
            point_key_counts[key] = dup_idx + 1
            if dup_idx:
                ts_ns += dup_idx

            rng = channel_ranges.get(channel)
            if rng is None:
                channel_ranges[channel] = [ts_ns, ts_ns]
            else:
                if ts_ns < rng[0]:
                    rng[0] = ts_ns
                if ts_ns > rng[1]:
                    rng[1] = ts_ns
            raw_rng = raw_channel_ranges.get(channel)
            if raw_rng is None:
                raw_channel_ranges[channel] = [raw_ts_ns, raw_ts_ns]
            else:
                if raw_ts_ns < raw_rng[0]:
                    raw_rng[0] = raw_ts_ns
                if raw_ts_ns > raw_rng[1]:
                    raw_rng[1] = raw_ts_ns
            candidates.append((channel, ts_ns, value, raw_ts_ns, tick_val))

        existing_by_channel = {}
        existing_raw_by_channel = {}
        bucket_q = json.dumps(influx_bucket)
        measurement_q = json.dumps(measurement)
        node_q = json.dumps(node_tag)
        source_q = json.dumps(source_tag)

        for channel, rng in channel_ranges.items():
            start_ns = int(rng[0])
            stop_ns = int(rng[1]) + 1
            start_iso = ns_to_iso_utc_fn(start_ns)
            stop_iso = ns_to_iso_utc_fn(stop_ns)
            if not start_iso or not stop_iso:
                existing_by_channel[channel] = set()
                continue
            ch_q = json.dumps(str(channel))
            start_q = json.dumps(start_iso)
            stop_q = json.dumps(stop_iso)
            flux = (
                f'from(bucket: {bucket_q})\n'
                f'  |> range(start: time(v: {start_q}), stop: time(v: {stop_q}))\n'
                f'  |> filter(fn: (r) => r._measurement == {measurement_q})\n'
                f'  |> filter(fn: (r) => r._field == "value")\n'
                f'  |> filter(fn: (r) => r.node_id == {node_q})\n'
                f'  |> filter(fn: (r) => r.channel == {ch_q})\n'
                f'  |> filter(fn: (r) => r.source == {source_q})\n'
                f'  |> map(fn: (r) => ({{ r with _value: uint(v: r._time) }}))\n'
                f'  |> keep(columns: ["_value"])'
            )
            exists = set()
            for rec in query_api.query_stream(query=flux, org=influx_org):
                try:
                    exists.add(int(rec.get_value()))
                except Exception:
                    continue
            existing_by_channel[channel] = exists

        for channel, rng in raw_channel_ranges.items():
            raw_start_ns = int(rng[0])
            raw_stop_ns = int(rng[1])
            ch_q = json.dumps(str(channel))
            flux = (
                f'from(bucket: {bucket_q})\n'
                f'  |> range(start: -3650d)\n'
                f'  |> filter(fn: (r) => r._measurement == {measurement_q})\n'
                f'  |> filter(fn: (r) => r._field == "node_ts_raw_ns" or r._field == "node_tick")\n'
                f'  |> filter(fn: (r) => r.node_id == {node_q})\n'
                f'  |> filter(fn: (r) => r.channel == {ch_q})\n'
                f'  |> filter(fn: (r) => r.source == {source_q})\n'
                f'  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")\n'
                f'  |> filter(fn: (r) => exists r.node_ts_raw_ns)\n'
                f'  |> filter(fn: (r) => r.node_ts_raw_ns >= {raw_start_ns}.0 and r.node_ts_raw_ns <= {raw_stop_ns}.0)\n'
                f'  |> keep(columns: ["node_ts_raw_ns", "node_tick"])'
            )
            raw_exists = {"pairs": set(), "raws": set()}
            for rec in query_api.query_stream(query=flux, org=influx_org):
                try:
                    vals = getattr(rec, "values", {}) or {}
                    raw_v = vals.get("node_ts_raw_ns")
                    if raw_v is None:
                        continue
                    raw_i = int(float(raw_v))
                    raw_exists["raws"].add(raw_i)
                    tick_v = vals.get("node_tick")
                    if tick_v is not None:
                        try:
                            raw_exists["pairs"].add((raw_i, int(float(tick_v))))
                        except Exception:
                            pass
                except Exception:
                    continue
            existing_raw_by_channel[channel] = raw_exists

        for channel, ts_ns, value, raw_ts_ns, tick_val in candidates:
            exists = existing_by_channel.get(channel)
            raw_exists = existing_raw_by_channel.get(channel)
            if raw_exists is not None:
                raw_i = int(raw_ts_ns)
                if tick_val is not None and (raw_i, int(tick_val)) in raw_exists["pairs"]:
                    total_skipped_existing += 1
                    continue
                if tick_val is None and raw_i in raw_exists["raws"]:
                    total_skipped_existing += 1
                    continue
            if exists is not None and ts_ns in exists:
                total_skipped_existing += 1
                continue

            point = (
                Point(measurement)
                .tag("device", node_tag)
                .tag("node_id", node_tag)
                .tag("channel", channel)
                .tag("source", source_tag)
                .field("value", value)
                .field("node_ts_raw_ns", int(raw_ts_ns))
                .time(ts_ns, WritePrecision.NS)
            )
            if tick_val is not None:
                point = point.field("node_tick", int(tick_val))
            batch.append(point)
            if exists is not None:
                exists.add(ts_ns)
            if raw_exists is not None:
                raw_i = int(raw_ts_ns)
                raw_exists["raws"].add(raw_i)
                if tick_val is not None:
                    raw_exists["pairs"].add((raw_i, int(tick_val)))

            if len(batch) >= max(1, int(export_batch_size)):
                write_api.write(influx_bucket, influx_org, batch)
                total_written += len(batch)
                batch = []

        if batch:
            write_api.write(influx_bucket, influx_org, batch)
            total_written += len(batch)

    return {"written": int(total_written), "skipped_existing": int(total_skipped_existing)}


__all__ = ["backfill_rows_to_influx_stream"]
