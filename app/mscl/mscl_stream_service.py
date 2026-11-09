import threading
import time
from collections import deque

from influxdb_client import InfluxDBClient, Point  # type: ignore
from influxdb_client.client.write_api import ASYNCHRONOUS, WriteOptions  # type: ignore
from influxdb_client.domain.write_precision import WritePrecision  # type: ignore


def run_stream_loop(
    *,
    stream_enabled,
    influx_url,
    influx_token,
    influx_org,
    influx_bucket,
    measurement,
    source_radio,
    read_timeout_ms,
    idle_sleep,
    batch_size,
    flush_interval_ms,
    queue_max,
    queue_wait_ms,
    drop_warn_sec,
    drop_log_throttle_sec,
    log_interval_sec,
    only_channel_1,
    state,
    log_func,
    internal_connect,
    mark_base_disconnected,
    metric_inc,
    metric_set,
    metric_max,
    point_channel_fn,
    point_value_fn,
    point_time_ns_fn,
):
    if not stream_enabled:
        log_func("[mscl-stream] Disabled via MSCL_STREAM_ENABLED")
        return
    if not all([influx_token, influx_org, influx_bucket]):
        log_func("[mscl-stream] Missing INFLUX_TOKEN / INFLUX_ORG / INFLUX_BUCKET; stream disabled")
        return

    log_func(
        f"[mscl-stream] Influx target: {influx_url} bucket={influx_bucket} org={influx_org} measurement={measurement}"
    )
    db_client = InfluxDBClient(url=influx_url, token=influx_token, org=influx_org)
    write_api = db_client.write_api(
        write_options=WriteOptions(
            batch_size=batch_size,
            flush_interval=flush_interval_ms,
            jitter_interval=200,
            retry_interval=1000,
            max_retries=3,
            max_retry_delay=10000,
            max_retry_time=60000,
            exponential_base=2,
        ),
        write_type=ASYNCHRONOUS,
    )

    queue_cond = threading.Condition()
    packet_queue = deque()
    last_ch1_ts = 0.0
    last_drop_log_ts = 0.0
    last_batch_log_ts = 0.0
    last_batch_node_ids = []
    last_diag = {}

    def note_diag(channel, value):
        last_diag[channel] = value

    def _stream_prefix(node_ids):
        ids = [str(x) for x in node_ids if str(x).strip()]
        ids = sorted(set(ids), key=lambda x: int(x) if x.isdigit() else x)
        if not ids:
            return ""
        if len(ids) == 1:
            return f"[node={ids[0]}] | "
        return f"[nodes={','.join(ids)}] | "

    def maybe_log_batch(now_ts, channel_counts, point_count, node_ids):
        nonlocal last_batch_log_ts, last_batch_node_ids
        if (now_ts - last_batch_log_ts) < log_interval_sec:
            return
        last_batch_log_ts = now_ts
        last_batch_node_ids = list(node_ids or [])
        channels_txt = ", ".join(f"{k}:{v}" for k, v in sorted(channel_counts.items()))
        log_func(f"{_stream_prefix(node_ids)}[mscl-stream] Logged {point_count} points ({channels_txt})")

    def packet_rate_label(packet):
        for getter in (
            lambda: packet.sampleRate().prettyStr(),
            lambda: packet.sampleRate().toString(),
            lambda: str(packet.sampleRate()),
        ):
            try:
                s = str(getter()).strip()
                if s:
                    return s
            except Exception:
                continue
        return "unknown"

    def maybe_log_drop(now_ts):
        nonlocal last_drop_log_ts
        if last_ch1_ts <= 0:
            return
        gap = now_ts - last_ch1_ts
        if gap < drop_warn_sec:
            return
        if now_ts - last_drop_log_ts < drop_log_throttle_sec:
            return
        last_drop_log_ts = now_ts
        diag_summary = ", ".join(
            f"{k}={last_diag.get(k)}"
            for k in (
                "diagnostic_state",
                "diagnostic_syncFailures",
                "diagnostic_totalDroppedPackets",
                "diagnostic_lowBatteryFlag",
                "diagnostic_memoryFull",
            )
        )
        log_func(f"{_stream_prefix(last_batch_node_ids)}[mscl-stream] Warning: no ch1 data for {gap:.1f}s; {diag_summary}")

    def reader_loop():
        backoff = 1.0
        backoff_max = 10.0
        disconnected = True
        while True:
            try:
                pause_until = float(getattr(state, "STREAM_PAUSE_UNTIL", 0.0) or 0.0)
                now_ts = time.time()
                if now_ts < pause_until:
                    time.sleep(min(0.25, max(0.05, pause_until - now_ts)))
                    continue

                ok, _ = internal_connect()
                if not ok or state.BASE_STATION is None:
                    metric_inc("base_reconnect_attempts")
                    disconnected = True
                    time.sleep(backoff)
                    backoff = min(backoff_max, backoff * 1.7)
                    continue
                if disconnected:
                    metric_inc("base_reconnect_successes")
                    disconnected = False

                with state.OP_LOCK:
                    base_station = state.BASE_STATION
                    if base_station is None:
                        time.sleep(idle_sleep)
                        continue
                    packets = base_station.getData(read_timeout_ms)

                if not packets:
                    backoff = 1.0
                    time.sleep(idle_sleep)
                    continue

                with queue_cond:
                    packet_queue.extend(packets)
                    dropped = 0
                    while len(packet_queue) > queue_max:
                        packet_queue.popleft()
                        dropped += 1
                    q_depth = len(packet_queue)
                    queue_cond.notify_all()
                metric_inc("stream_packets_read", len(packets))
                metric_set("stream_queue_depth", q_depth)
                metric_max("stream_queue_hwm", q_depth)
                if dropped:
                    metric_inc("stream_queue_dropped_packets", dropped)
                backoff = 1.0
            except Exception as e:
                log_func(f"[mscl-stream] Reader error: {e}")
                metric_inc("stream_reader_errors")
                mark_base_disconnected()
                disconnected = True
                time.sleep(backoff)
                backoff = min(backoff_max, backoff * 1.7)

    threading.Thread(target=reader_loop, daemon=True).start()

    while True:
        try:
            with queue_cond:
                if not packet_queue:
                    queue_cond.wait(timeout=queue_wait_ms / 1000.0)
                packets = []
                while packet_queue:
                    packets.append(packet_queue.popleft())

            if not packets:
                time.sleep(idle_sleep)
                continue

            raw_rows = []
            channel_counts = {}
            packet_rate_counts = {}
            packet_node_ids = set()
            for packet in packets:
                node_address = str(packet.nodeAddress())
                packet_node_ids.add(node_address)
                rate_lbl = packet_rate_label(packet)
                packet_rate_counts[rate_lbl] = packet_rate_counts.get(rate_lbl, 0) + 1
                for dp in packet.data():
                    channel = point_channel_fn(dp)
                    if only_channel_1 and channel not in ("channel_1", "ch1"):
                        continue
                    value = point_value_fn(dp)
                    if value is None:
                        continue
                    if channel.startswith("diagnostic_"):
                        note_diag(channel, value)
                    t_ns = point_time_ns_fn(dp)
                    raw_rows.append(
                        {
                            "node_id": node_address,
                            "channel": channel,
                            "source": source_radio,
                            "value": value,
                            "t_ns": int(t_ns),
                        }
                    )
                    channel_counts[channel] = channel_counts.get(channel, 0) + 1
                    if channel in ("channel_1", "ch1"):
                        last_ch1_ts = time.time()

            points = []
            point_key_counts = {}
            for row in raw_rows:
                t_ns = int(row["t_ns"])
                key = (row["node_id"], row["channel"], t_ns)
                dup_idx = point_key_counts.get(key, 0)
                point_key_counts[key] = dup_idx + 1
                if dup_idx:
                    t_ns += dup_idx
                point = (
                    Point(measurement)
                    .tag("device", str(row["node_id"]))
                    .tag("node_id", row["node_id"])
                    .tag("channel", row["channel"])
                    .tag("source", row["source"])
                    .field("value", row["value"])
                    .field("node_ts_raw_ns", int(row["t_ns"]))
                    .time(t_ns, WritePrecision.NS)
                )
                points.append(point)

            if points:
                write_api.write(influx_bucket, influx_org, points)
                metric_inc("stream_write_calls")
                metric_inc("stream_points_written", len(points))
                maybe_log_batch(time.time(), channel_counts, len(points), packet_node_ids)
                if packet_rate_counts:
                    rate_txt = ", ".join(f"{k}:{v}" for k, v in sorted(packet_rate_counts.items()))
                    log_func(f"{_stream_prefix(packet_node_ids)}[mscl-stream] Packet rates ({rate_txt})")
            maybe_log_drop(time.time())
        except Exception as e:
            log_func(f"[mscl-stream] Writer error: {e}")
            metric_inc("stream_writer_errors")
            time.sleep(idle_sleep)


__all__ = ["run_stream_loop"]
