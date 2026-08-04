import logging
import time
import threading
from pathlib import Path

from flask import Flask, render_template, request, jsonify, send_file, Response  # type: ignore

from mscl_constants import (
    COMM_PROTOCOL_MAP,
    DATA_MODE_LABELS,
    DEFAULT_MODE_LABELS,
    INPUT_RANGE_LABELS,
    LOW_PASS_LABELS,
    PRIMARY_INPUT_RANGES,
    PRIMARY_UNIT_ORDER,
    RATE_MAP,
    RTD_SENSOR_LABELS,
    RTD_WIRE_LABELS,
    SAMPLING_MODE_LABELS,
    SAMPLING_MODE_MAP,
    STORAGE_LIMIT_LABELS,
    TC_LINK_200_RATE_ENUMS,
    TEMP_UNIT_ORDER,
    THERMISTOR_SENSOR_LABELS,
    THERMOCOUPLE_SENSOR_LABELS,
    TRANSDUCER_LABELS,
    TX_POWER_ENUM_TO_DBM,
    UNIT_LABELS,
    _is_temp_unit,
    _unit_family,
    _wt,
)
from mscl_stream_helpers import (
    coerce_logged_sweeps as _coerce_logged_sweeps,
    logged_sweep_rows as _logged_sweep_rows,
    ns_to_iso_utc as _ns_to_iso_utc,
    point_channel as _point_channel,
    point_time_ns as _point_time_ns,
    point_value as _point_value,
)
from mscl_rate_helpers import (
    filter_sample_rates_for_model as _filter_sample_rates_for_model_impl,
    sample_rate_label as _sample_rate_label_impl,
)
from mscl_export_helpers import (
    filter_rows_by_host_window,
    parse_iso_utc_to_ns,
    resolve_export_time_window,
)
from mscl_backfill_service import backfill_rows_to_influx_stream as backfill_rows_to_influx_stream_service
from mscl_sampling_service import (
    schedule_idle_after as schedule_idle_after_service,
    send_idle_sensorconnect_style as send_idle_sensorconnect_style_service,
    start_sampling_via_sync_network as start_sampling_via_sync_network_service,
    start_sampling_via_sync_network_multi as start_sampling_via_sync_network_multi_service,
)
from mscl_sampling_run_service import (
    start_sampling_group_run as start_sampling_group_run_service,
    start_sampling_run as start_sampling_run_service,
)
from mscl_status_service import build_status_payload
from mscl_health_service import build_health_payload
from mscl_export_request_helpers import ExportRequestValidationError, parse_export_storage_request
from mscl_write_config_service import build_write_config
from mscl_write_cache_service import update_write_cache
from mscl_write_request_helpers import WriteRequestValidationError, validate_write_request
from mscl_write_retry_service import run_write_retry_loop
from mscl_tx_power_helpers import normalize_tx_power
from mscl_write_payload_helpers import normalize_write_payload
from mscl_write_apply_service import apply_write_connected
from mscl_api_helpers import cached_node_snapshot, map_export_storage_error
from mscl_export_storage_service import execute_export_storage_connected
from mscl_ui_helpers import (
    collection_method_text,
    filter_default_modes_for_model,
    is_tc_link_200_oem_model,
    sampling_duration_to_seconds,
    tx_power_options_for_model,
)
from mscl_settings import (
    INFLUX_BUCKET,
    INFLUX_ORG,
    INFLUX_TOKEN,
    INFLUX_URL,
    MSCL_EXPORT_INFLUX_BATCH,
    MSCL_MEASUREMENT,
    MSCL_NODE_IDS,
    MSCL_ONLY_CHANNEL_1,
    MSCL_SOURCE_NODE_EXPORT,
    MSCL_SOURCE_RADIO,
    MSCL_STREAM_BATCH_SIZE,
    MSCL_STREAM_DROP_LOG_THROTTLE_SEC,
    MSCL_STREAM_DROP_WARN_SEC,
    MSCL_STREAM_ENABLED,
    MSCL_STREAM_FLUSH_INTERVAL_MS,
    MSCL_STREAM_IDLE_SLEEP,
    MSCL_STREAM_LOG_INTERVAL_SEC,
    MSCL_STREAM_QUEUE_MAX,
    MSCL_STREAM_QUEUE_WAIT_MS,
    MSCL_STREAM_READ_TIMEOUT_MS,
)

import mscl_state as state
import MSCL as mscl  # type: ignore

log = state.log
internal_connect = state.internal_connect
ensure_beacon_on = state.ensure_beacon_on
ch1_mask = state.ch1_mask
ch2_mask = state.ch2_mask
set_idle_with_retry = state.set_idle_with_retry
_get_temp_sensor_options = state._get_temp_sensor_options
_set_temp_sensor_options = state._set_temp_sensor_options
_filter_default_modes = state._filter_default_modes
_feature_supported = state._feature_supported
_node_state_info = state._node_state_info
close_base_station = state.close_base_station
mark_base_disconnected = state.mark_base_disconnected
metric_inc = state.metric_inc
metric_set = state.metric_set
metric_max = state.metric_max
metric_snapshot = state.metric_snapshot

app = Flask(__name__)


@app.get("/device-common.css")
def device_common_css():
    mounted_path = Path("/shared/device-common.css")
    path = mounted_path if mounted_path.exists() else Path(__file__).with_name("static").joinpath("device-common.css")
    return path.read_text(encoding="utf-8"), 200, {"Content-Type": "text/css"}


# Suppress Flask request logs (GET/POST lines)
logging.getLogger("werkzeug").setLevel(logging.WARNING)


def _pause_stream_reader(seconds, reason=""):
    try:
        sec = max(0.0, float(seconds))
    except Exception:
        sec = 0.0
    if sec <= 0:
        return
    until = time.time() + sec
    prev = float(getattr(state, "STREAM_PAUSE_UNTIL", 0.0) or 0.0)
    if until > prev:
        state.STREAM_PAUSE_UNTIL = until
        if reason:
            log(f"[mscl-stream] pause {sec:.1f}s reason={reason}")
        else:
            log(f"[mscl-stream] pause {sec:.1f}s")


def _parse_iso_utc_to_ns(raw_value, name):
    try:
        return parse_iso_utc_to_ns(raw_value, name)
    except ValueError as e:
        if str(e).startswith("Missing "):
            raise
        raise ValueError(f"Invalid {name}. Use ISO datetime (example: 2026-02-11T12:00:00Z).")


def _backfill_rows_to_influx_stream(node_id, rows, source_tag=MSCL_SOURCE_NODE_EXPORT):
    return backfill_rows_to_influx_stream_service(
        node_id=node_id,
        rows=rows,
        source_tag=source_tag,
        influx_url=INFLUX_URL,
        influx_token=INFLUX_TOKEN,
        influx_org=INFLUX_ORG,
        influx_bucket=INFLUX_BUCKET,
        measurement=MSCL_MEASUREMENT,
        export_batch_size=MSCL_EXPORT_INFLUX_BATCH,
        ns_to_iso_utc_fn=_ns_to_iso_utc,
    )


def _stream_loop():
    from mscl_stream_service import run_stream_loop

    run_stream_loop(
        stream_enabled=MSCL_STREAM_ENABLED,
        influx_url=INFLUX_URL,
        influx_token=INFLUX_TOKEN,
        influx_org=INFLUX_ORG,
        influx_bucket=INFLUX_BUCKET,
        measurement=MSCL_MEASUREMENT,
        source_radio=MSCL_SOURCE_RADIO,
        read_timeout_ms=MSCL_STREAM_READ_TIMEOUT_MS,
        idle_sleep=MSCL_STREAM_IDLE_SLEEP,
        batch_size=MSCL_STREAM_BATCH_SIZE,
        flush_interval_ms=MSCL_STREAM_FLUSH_INTERVAL_MS,
        queue_max=MSCL_STREAM_QUEUE_MAX,
        queue_wait_ms=MSCL_STREAM_QUEUE_WAIT_MS,
        drop_warn_sec=MSCL_STREAM_DROP_WARN_SEC,
        drop_log_throttle_sec=MSCL_STREAM_DROP_LOG_THROTTLE_SEC,
        log_interval_sec=MSCL_STREAM_LOG_INTERVAL_SEC,
        only_channel_1=MSCL_ONLY_CHANNEL_1,
        state=state,
        log_func=log,
        internal_connect=internal_connect,
        mark_base_disconnected=mark_base_disconnected,
        metric_inc=metric_inc,
        metric_set=metric_set,
        metric_max=metric_max,
        point_channel_fn=_point_channel,
        point_value_fn=_point_value,
        point_time_ns_fn=_point_time_ns,
    )


def _start_streamer():
    t = threading.Thread(target=_stream_loop, daemon=True)
    t.start()


def send_idle_sensorconnect_style(node, node_id, stage_tag):
    return send_idle_sensorconnect_style_service(
        node=node,
        node_id=node_id,
        stage_tag=stage_tag,
        mscl_mod=mscl,
        log_func=log,
    )


def _start_sampling_via_sync_network(node, node_id):
    return start_sampling_via_sync_network_service(
        node=node,
        node_id=node_id,
        mscl_mod=mscl,
        state=state,
        log_func=log,
    )


def _start_sampling_via_sync_network_multi(nodes, node_ids):
    return start_sampling_via_sync_network_multi_service(
        nodes=nodes,
        node_ids=node_ids,
        mscl_mod=mscl,
        state=state,
        log_func=log,
    )

def _schedule_idle_after(node_id, seconds, token):
    return schedule_idle_after_service(
        node_id=node_id,
        seconds=seconds,
        token=token,
        state=state,
        log_func=log,
        internal_connect=internal_connect,
        ensure_beacon_on=ensure_beacon_on,
        node_state_info_fn=_node_state_info,
        send_idle_fn=send_idle_sensorconnect_style,
        mscl_mod=mscl,
    )


def _is_sampling_active(node_id):
    run = state.SAMPLE_RUNS.get(node_id)
    if not run or run.get("state") != "running":
        return False
    duration_sec = int(run.get("duration_sec") or 0)
    if duration_sec <= 0:
        return True
    started_at = int(run.get("started_at") or 0)
    if started_at <= 0:
        return True
    return (time.time() - started_at) < duration_sec


def _set_sampling_mode_on_node(cfg, mode_key):
    mode_key = str(mode_key or "transmit").lower()
    mode_value = SAMPLING_MODE_MAP.get(mode_key, SAMPLING_MODE_MAP["transmit"])
    errs = []
    try:
        cfg.dataCollectionMethod(int(mode_value))
        return mode_value, None
    except Exception as e:
        errs.append(str(e))

    # Compatibility fallback for nodes that only expose dataMode in config.
    # Keep this fallback for transmit only to avoid mapping log-modes to derived data.
    if mode_key == "transmit":
        try:
            cfg.dataMode(int(getattr(mscl.WirelessTypes, "dataMode_raw", 1)))
            return mode_value, None
        except Exception as e:
            errs.append(str(e))
    return None, " | ".join(errs) if errs else "no setter available"


def _set_sampling_data_type_on_node(cfg, data_type):
    data_type = str(data_type or "float").strip().lower()
    errs = []
    if data_type == "calibrated":
        try:
            cfg.dataFormat(int(getattr(mscl.WirelessTypes, "dataFormat_cal_float")))
            return "calibrated", None
        except Exception as e:
            errs.append(str(e))
            return None, " | ".join(errs) if errs else "calibrated format setter is unavailable"
    return "float", None


def _classify_data_type_from_format(format_value):
    try:
        fv = int(format_value)
    except Exception:
        return "float"
    cal_candidates = {
        int(getattr(mscl.WirelessTypes, "dataFormat_cal_float", -1)),
        int(getattr(mscl.WirelessTypes, "dataFormat_cal_int16_x10", -1)),
    }
    return "calibrated" if fv in cal_candidates else "float"


def _filter_sample_rates_for_model(model, supported_rates, current_rate):
    return _filter_sample_rates_for_model_impl(
        model=model,
        supported_rates=supported_rates,
        current_rate=current_rate,
        rate_map=RATE_MAP,
        tc_link_200_rate_enums=TC_LINK_200_RATE_ENUMS,
    )


def _sample_rate_label(rate_enum, rate_obj=None):
    return _sample_rate_label_impl(rate_enum=rate_enum, rate_obj=rate_obj, rate_map=RATE_MAP)


def _start_sampling_run(node_id, body):
    return start_sampling_run_service(
        node_id=node_id,
        body=body,
        internal_connect=internal_connect,
        state=state,
        ensure_beacon_on=ensure_beacon_on,
        mscl_mod=mscl,
        log_func=log,
        rate_map=RATE_MAP,
        sampling_mode_labels=SAMPLING_MODE_LABELS,
        duration_to_seconds_fn=sampling_duration_to_seconds,
        set_sampling_mode_fn=_set_sampling_mode_on_node,
        set_sampling_data_type_fn=_set_sampling_data_type_on_node,
        start_sampling_via_sync_network_fn=_start_sampling_via_sync_network,
        schedule_idle_after_fn=_schedule_idle_after,
    )


def _start_sampling_group_run(body):
    return start_sampling_group_run_service(
        node_ids=MSCL_NODE_IDS,
        body=body,
        internal_connect=internal_connect,
        state=state,
        ensure_beacon_on=ensure_beacon_on,
        mscl_mod=mscl,
        log_func=log,
        rate_map=RATE_MAP,
        sampling_mode_labels=SAMPLING_MODE_LABELS,
        duration_to_seconds_fn=sampling_duration_to_seconds,
        set_sampling_mode_fn=_set_sampling_mode_on_node,
        set_sampling_data_type_fn=_set_sampling_data_type_on_node,
        start_sampling_via_sync_network_multi_fn=_start_sampling_via_sync_network_multi,
        schedule_idle_after_fn=_schedule_idle_after,
    )

@app.route('/')
def index():
    return render_template("mscl_web_config.html", mscl_node_ids=MSCL_NODE_IDS)

@app.route('/api/connect', methods=['POST'])
def api_connect():
    with state.OP_LOCK:
        s, p = internal_connect()
        return jsonify(success=s, port=p)


@app.route('/api/disconnect', methods=['POST'])
def api_disconnect():
    with state.OP_LOCK:
        state.close_base_station()
        state.LAST_BASE_STATUS.update({"connected": False, "message": "Disconnected", "ts": time.strftime("%H:%M:%S")})
        return jsonify(success=True, message="Disconnected")

@app.route('/api/status')
def api_status():
    with state.OP_LOCK:
        payload = build_status_payload(state=state, now=time.time())
        return jsonify(**payload)

@app.route('/api/reconnect', methods=['POST'])
def api_reconnect():
    with state.OP_LOCK:
        mark_base_disconnected()
        ok, msg = internal_connect(force_ping=True)
        return jsonify(success=bool(ok), message=msg)

@app.route('/api/beacon', methods=['POST'])
def api_beacon():
    with state.OP_LOCK:
        ok, msg = internal_connect()
        if not ok or state.BASE_STATION is None:
            return jsonify(success=False, error=f"Base station not connected: {msg}")
        body = request.json or {}
        requested = body.get("enabled", None)
        if requested is None:
            target = not bool(state.BASE_BEACON_STATE)
        else:
            target = bool(requested)
        try:
            if target:
                state.BASE_STATION.enableBeacon()
                state.BASE_BEACON_STATE = True
                log("[mscl-web] [BEACON] enabled")
                return jsonify(success=True, beacon_state=True, message="Beacon ON")
            disable_methods = ("disableBeacon", "setBeaconOff")
            disabled = False
            for m in disable_methods:
                fn = getattr(state.BASE_STATION, m, None)
                if callable(fn):
                    fn()
                    disabled = True
                    break
            if not disabled:
                return jsonify(success=False, error="Beacon OFF is not supported by this MSCL API build")
            state.BASE_BEACON_STATE = False
            log("[mscl-web] [BEACON] disabled")
            return jsonify(success=True, beacon_state=False, message="Beacon OFF")
        except Exception as e:
            return jsonify(success=False, error=str(e))

@app.route('/api/diagnostics/<int:node_id>')
def api_diagnostics(node_id):
    with state.OP_LOCK:
        ok, msg = internal_connect()
        if not ok or state.BASE_STATION is None:
            return jsonify(success=False, error=f"Base station not connected: {msg}")
        try:
            node = mscl.WirelessNode(node_id, state.BASE_STATION)
            features = node.features()
            flags = [
                ("supportsInputRange", "supportsInputRange"),
                ("supportsLowPassFilter", "supportsLowPassFilter"),
                ("supportsCommunicationProtocol", "supportsCommunicationProtocol"),
                ("supportsTempSensorOptions", "supportsTempSensorOptions"),
            ]
            out = []
            for label, fn in flags:
                try:
                    out.append({"name": label, "value": bool(getattr(features, fn)())})
                except Exception:
                    out.append({"name": label, "value": False})
            try:
                raw_modes = []
                try:
                    raw_modes = features.dataModes()
                except Exception:
                    raw_modes = []
                mode_parts = []
                for m in raw_modes:
                    mi = int(m)
                    mode_parts.append(f"{mi} ({DATA_MODE_LABELS.get(mi, f'Value {mi}')})")
                out.append({
                    "name": "supportedDataModes",
                    "value": ", ".join(mode_parts) if mode_parts else "N/A",
                })
            except Exception:
                out.append({"name": "supportedDataModes", "value": "N/A"})
            return jsonify(success=True, flags=out)
        except Exception as e:
            return jsonify(success=False, error=str(e))

@app.route('/api/logs')
def api_logs():
    return jsonify(logs=state.LOG_BUFFER[-state.LOG_MAX:])

@app.route('/api/metrics')
def api_metrics():
    metrics = metric_snapshot()
    metrics["node_cache_size"] = len(state.NODE_READ_CACHE)
    metrics["sampling_runs_count"] = len(state.SAMPLE_RUNS)
    metrics["idle_in_progress_count"] = len(state.IDLE_IN_PROGRESS)
    metrics["base_connected"] = bool(state.BASE_STATION is not None)
    metrics["base_port"] = state.CURRENT_PORT
    return jsonify(metrics=metrics)


@app.route('/api/health')
def api_health():
    with state.OP_LOCK:
        payload = build_health_payload(state=state, now=time.time(), metric_snapshot_fn=metric_snapshot)
        return jsonify(**payload)

@app.route('/api/read/<int:node_id>')
def api_read(node_id):
    read_tag = "READ"
    max_attempts = 5
    last_err = None
    log(f"[mscl-web] [{read_tag}] request node_id={node_id}")
    with state.OP_LOCK:
        cached = state.NODE_READ_CACHE.get(node_id, {})
        refresh_eeprom = True
        for attempt in range(1, max_attempts + 1):
            if attempt > 1:
                log(f"[mscl-web] [{read_tag}] retry {attempt}/{max_attempts} node_id={node_id}")
            ok, msg = internal_connect()
            if not ok or state.BASE_STATION is None:
                last_err = f"Base station not connected: {msg}"
                log(f"[mscl-web] [{read_tag}] failed: {last_err}")
                time.sleep(0.5)
                continue
            try:
                ensure_beacon_on()
                node = mscl.WirelessNode(node_id, state.BASE_STATION)
                node.readWriteRetries(15)
                # 2. Critical values (use cache-first for EEPROM-heavy fields)
                current_rate = cached.get("current_rate")
                if refresh_eeprom or current_rate is None:
                    try:
                        current_rate = int(node.getSampleRate())
                    except Exception as e:
                        last_err = str(e)
                        log(f"[mscl-web] [{read_tag}] warn node_id={node_id}: getSampleRate failed (1st): {last_err}")
                        time.sleep(1.0)
                        try:
                            current_rate = int(node.getSampleRate())
                        except Exception as e2:
                            last_err = str(e2)
                            log(f"[mscl-web] [{read_tag}] error node_id={node_id}: getSampleRate failed (2nd): {last_err}")
                            if "EEPROM" in last_err:
                                metric_inc("eeprom_retries_read")
                            if "EEPROM" not in last_err:
                                mark_base_disconnected()
                                time.sleep(0.5)
                                continue
                try:
                    active_mask = node.getActiveChannels()
                except Exception:
                    active_mask = None
        
                # 3. Remaining fields are best-effort (do not fail read on EEPROM errors)
                model = cached.get("model", "TC-Link-200")
                if refresh_eeprom or "model" not in cached:
                    try:
                        model = node.model()
                    except Exception as e:
                        log(f"[mscl-web] [{read_tag}] warn node_id={node_id}: model read failed: {e}")
        
                sn = cached.get("sn", "N/A")
                try:
                    sn = str(node.nodeAddress())
                except Exception as e:
                    log(f"[mscl-web] [{read_tag}] warn node_id={node_id}: serial read failed: {e}")
        
                fw = cached.get("fw", "N/A")
                if refresh_eeprom or "fw" not in cached:
                    try:
                        fw = str(node.firmwareVersion())
                    except Exception as e:
                        log(f"[mscl-web] [{read_tag}] warn node_id={node_id}: firmware read failed: {e}")
        
                current_power = cached.get("current_power", 16)
                current_power_enum = cached.get("current_power_enum")
                if refresh_eeprom or "current_power" not in cached:
                    try: 
                        p_raw = int(node.getTransmitPower())
                        current_power_enum = p_raw
                        current_power = TX_POWER_ENUM_TO_DBM.get(p_raw, 16)
                    except Exception as e:
                        log(f"[mscl-web] [{read_tag}] warn node_id={node_id}: transmit power read failed: {e}")
                tx_power_options = tx_power_options_for_model(model, current_power)
                comm_protocol = cached.get("comm_protocol")
                comm_protocol_text = cached.get("comm_protocol_text")
                if refresh_eeprom or "comm_protocol" not in cached:
                    try:
                        cp = int(node.communicationProtocol())
                        comm_protocol = cp
                        comm_protocol_text = COMM_PROTOCOL_MAP.get(cp, f"Value {cp}")
                    except Exception as e:
                        log(f"[mscl-web] [{read_tag}] warn node_id={node_id}: communicationProtocol read failed: {e}")
            
                # Optional status fields (best-effort)
                try:
                    region = str(node.regionCode())
                except Exception:
                    region = None
                try:
                    last_comm = str(node.lastCommunicationTime()).split(".")[0]
                except Exception:
                    last_comm = None
                node_state, state_text, _ = _node_state_info(node)
                try:
                    node_address = int(node.nodeAddress())
                except Exception:
                    node_address = None
                try:
                    freq_raw = node.frequency()
                    try:
                        freq_ch = int(freq_raw)
                        frequency = f"{freq_ch} ({2404 + 2 * freq_ch} MHz)"
                    except Exception:
                        frequency = str(freq_raw)
                except Exception:
                    frequency = None
                storage_capacity_raw = cached.get("storage_capacity_raw")
                if refresh_eeprom or "storage_capacity_raw" not in cached:
                    try:
                        storage_capacity_raw = int(node.dataStorageSize())
                    except Exception:
                        pass
                try:
                    storage_pct = round(float(node.percentFull()), 2)
                except Exception:
                    storage_pct = None
                sampling_mode = cached.get("sampling_mode")
                sampling_mode_raw = cached.get("sampling_mode_raw")
                if refresh_eeprom or "sampling_mode" not in cached:
                    try:
                        sampling_mode_val = node.getSamplingMode()
                        try:
                            sampling_mode_raw = int(sampling_mode_val)
                        except Exception:
                            sampling_mode_raw = None
                        sampling_mode = "sync" if sampling_mode_val == mscl.WirelessTypes.samplingMode_sync else "non_sync"
                    except Exception:
                        pass
                current_data_mode = cached.get("current_data_mode")
                data_mode_options = cached.get("data_mode_options", [])
                current_collection_method = cached.get("current_collection_method")
                if refresh_eeprom or "current_data_mode" not in cached:
                    try:
                        current_data_mode = int(node.getDataMode())
                    except Exception:
                        pass
                if refresh_eeprom or "current_collection_method" not in cached:
                    try:
                        current_collection_method = int(node.getDataCollectionMethod())
                    except Exception:
                        pass
                if refresh_eeprom or not data_mode_options:
                    try:
                        features = node.features()
                        modes = []
                        try:
                            modes = features.dataModes()
                        except Exception:
                            modes = []
                        opts = []
                        for m in modes:
                            mi = int(m)
                            opts.append({"value": mi, "label": DATA_MODE_LABELS.get(mi, f"Value {mi}")})
                        data_mode_options = opts
                    except Exception:
                        if not data_mode_options:
                            data_mode_options = []
                if current_data_mode is not None:
                    if all(x.get("value") != int(current_data_mode) for x in data_mode_options):
                        data_mode_options.insert(
                            0,
                            {
                                "value": int(current_data_mode),
                                "label": DATA_MODE_LABELS.get(int(current_data_mode), f"Value {int(current_data_mode)}"),
                            },
                        )
                if not data_mode_options:
                    data_mode_options = [
                        {"value": 1, "label": DATA_MODE_LABELS[1]},
                        {"value": 2, "label": DATA_MODE_LABELS[2]},
                        {"value": 3, "label": DATA_MODE_LABELS[3]},
                    ]
                data_mode_text = (
                    DATA_MODE_LABELS.get(int(current_data_mode), f"Value {int(current_data_mode)}")
                    if current_data_mode is not None
                    else None
                )
                current_collection_method_text = collection_method_text(
                    current_collection_method,
                    SAMPLING_MODE_MAP,
                    SAMPLING_MODE_LABELS,
                )
                current_data_format = cached.get("current_data_format")
                if refresh_eeprom or "current_data_format" not in cached:
                    try:
                        current_data_format = int(node.getDataFormat())
                    except Exception:
                        pass
                current_data_type = _classify_data_type_from_format(current_data_format)
                current_input_range = cached.get("current_input_range")
                supported_input_ranges = cached.get("supported_input_ranges", [])
                if refresh_eeprom or "current_input_range" not in cached:
                    try:
                        current_input_range = int(node.getInputRange(ch1_mask()))
                    except Exception as e:
                        log(f"[mscl-web] [{read_tag}] warn node_id={node_id}: getInputRange(ch1) failed: {e}")
                if refresh_eeprom or not supported_input_ranges:
                    try:
                        features = node.features()
                        ir_values = []
                        try:
                            ir_values = features.inputRanges()
                        except Exception:
                            ir_values = []
                        supported_input_ranges = []
                        for ir in ir_values:
                            ir_int = int(ir)
                            supported_input_ranges.append({
                                "value": ir_int,
                                "label": INPUT_RANGE_LABELS.get(ir_int, f"Value {ir_int}"),
                                "primary": ir_int in PRIMARY_INPUT_RANGES,
                            })
                        # Stable order: primary (SensorConnect top set) first, then others by value.
                        supported_input_ranges.sort(
                            key=lambda x: (0 if x.get("primary") else 1, int(x.get("value", 999999)))
                        )
                        if len(supported_input_ranges) <= 1:
                            existing = {int(x.get("value")) for x in supported_input_ranges if x.get("value") is not None}
                            for v in (99, 100, 101, 102, 103):
                                if v not in existing:
                                    supported_input_ranges.append({
                                        "value": v,
                                        "label": INPUT_RANGE_LABELS[v],
                                        "primary": True,
                                    })
                            supported_input_ranges.sort(
                                key=lambda x: (0 if x.get("primary") else 1, int(x.get("value", 999999)))
                            )
                        if current_input_range is not None and all(x.get("value") != int(current_input_range) for x in supported_input_ranges):
                            supported_input_ranges.insert(0, {
                                "value": int(current_input_range),
                                "label": INPUT_RANGE_LABELS.get(int(current_input_range), f"Value {int(current_input_range)}"),
                                "primary": int(current_input_range) in PRIMARY_INPUT_RANGES,
                            })
                    except Exception as e:
                        log(f"[mscl-web] [{read_tag}] warn node_id={node_id}: features/inputRanges failed: {e}")
                # Fallback: keep SensorConnect-like core list visible even when feature read fails.
                if not supported_input_ranges:
                    supported_input_ranges = [
                        {"value": 99, "label": INPUT_RANGE_LABELS[99], "primary": True},
                        {"value": 100, "label": INPUT_RANGE_LABELS[100], "primary": True},
                        {"value": 101, "label": INPUT_RANGE_LABELS[101], "primary": True},
                        {"value": 102, "label": INPUT_RANGE_LABELS[102], "primary": True},
                        {"value": 103, "label": INPUT_RANGE_LABELS[103], "primary": True},
                    ]
                    if current_input_range is not None and all(x.get("value") != int(current_input_range) for x in supported_input_ranges):
                        supported_input_ranges.insert(0, {
                            "value": int(current_input_range),
                            "label": INPUT_RANGE_LABELS.get(int(current_input_range), f"Value {int(current_input_range)}"),
                            "primary": int(current_input_range) in PRIMARY_INPUT_RANGES,
                        })

                current_low_pass = cached.get("current_low_pass")
                low_pass_options = cached.get("low_pass_options", [])
                try:
                    lp_raw = int(node.getLowPassFilter(ch1_mask()))
                    current_low_pass = lp_raw
                except Exception as e:
                    log(f"[mscl-web] [{read_tag}] warn node_id={node_id}: getLowPassFilter(ch1) failed: {e}")
                try:
                    features = node.features()
                    lpf = []
                    try:
                        lpf = features.lowPassFilters()
                    except Exception:
                        lpf = []
                    opts = []
                    for v in lpf:
                        vi = int(v)
                        opts.append({"value": vi, "label": LOW_PASS_LABELS.get(vi, f"Value {vi}")})
                    if not opts:
                        opts = [{"value": 294, "label": LOW_PASS_LABELS.get(294, "294 Hz")}]
                    low_pass_options = opts
                except Exception as e:
                    log(f"[mscl-web] [{read_tag}] warn node_id={node_id}: features/lowPassFilters failed: {e}")
                    if not low_pass_options:
                        low_pass_options = [{"value": 294, "label": LOW_PASS_LABELS.get(294, "294 Hz")}]
                if current_low_pass is not None and all(x.get("value") != int(current_low_pass) for x in low_pass_options):
                    low_pass_options.insert(0, {"value": int(current_low_pass), "label": LOW_PASS_LABELS.get(int(current_low_pass), f"Value {int(current_low_pass)}")})
                if current_low_pass is None and low_pass_options:
                    current_low_pass = int(low_pass_options[0]["value"])

                current_unit = cached.get("current_unit")
                unit_options = cached.get("unit_options", [])
                if refresh_eeprom or "current_unit" not in cached:
                    try:
                        current_unit = int(node.getUnit(ch1_mask()))
                    except Exception as e:
                        log(f"[mscl-web] [{read_tag}] warn node_id={node_id}: getUnit(ch1) failed: {e}")
                        try:
                            current_unit = int(node.getUnit())
                        except Exception:
                            pass
                if refresh_eeprom or not unit_options:
                    try:
                        features = node.features()
                        values = []
                        for getter in (lambda: features.units(ch1_mask()), lambda: features.units()):
                            try:
                                values = getter()
                                if values:
                                    break
                            except Exception:
                                continue
                        unit_options = []
                        for v in values:
                            vi = int(v)
                            unit_options.append({"value": vi, "label": UNIT_LABELS.get(vi, f"Value {vi}")})
                    except Exception as e:
                        log(f"[mscl-web] [{read_tag}] warn node_id={node_id}: features/units failed: {e}")
                # SensorConnect-like behavior: keep core engineering units visible.
                if len(unit_options) <= 1:
                    existing = {int(x.get("value")) for x in unit_options if x.get("value") is not None}
                    for target_label in PRIMARY_UNIT_ORDER:
                        for unit_val, unit_label in UNIT_LABELS.items():
                            if _unit_family(unit_label) == target_label and int(unit_val) not in existing:
                                unit_options.append({"value": int(unit_val), "label": unit_label})
                                existing.add(int(unit_val))
                                break
                if unit_options:
                    unit_options.sort(
                        key=lambda x: (
                            PRIMARY_UNIT_ORDER.index(_unit_family(x.get("label"))) if _unit_family(x.get("label")) in PRIMARY_UNIT_ORDER else 99,
                            str(x.get("label")),
                            int(x.get("value", 999999)),
                        )
                    )
                if current_unit is not None and all(x.get("value") != int(current_unit) for x in unit_options):
                    unit_options.insert(0, {"value": int(current_unit), "label": UNIT_LABELS.get(int(current_unit), f"Value {int(current_unit)}")})
                if not unit_options and current_unit is not None:
                    unit_options = [{"value": int(current_unit), "label": UNIT_LABELS.get(int(current_unit), f"Value {int(current_unit)}")}]

                current_cjc_unit = cached.get("current_cjc_unit")
                cjc_unit_options = cached.get("cjc_unit_options", [])
                if refresh_eeprom or "current_cjc_unit" not in cached:
                    try:
                        current_cjc_unit = int(node.getUnit(ch2_mask()))
                    except Exception as e:
                        log(f"[mscl-web] [{read_tag}] warn node_id={node_id}: getUnit(ch2) failed: {e}")
                if refresh_eeprom or not cjc_unit_options:
                    try:
                        features = node.features()
                        values = []
                        for getter in (lambda: features.units(ch2_mask()), lambda: features.units()):
                            try:
                                values = getter()
                                if values:
                                    break
                            except Exception:
                                continue
                        cjc_unit_options = []
                        for v in values:
                            vi = int(v)
                            lbl = UNIT_LABELS.get(vi, f"Value {vi}")
                            if _is_temp_unit(lbl):
                                cjc_unit_options.append({"value": vi, "label": lbl})
                    except Exception as e:
                        log(f"[mscl-web] [{read_tag}] warn node_id={node_id}: features/units(ch2) failed: {e}")
                if len(cjc_unit_options) <= 1:
                    existing = {int(x.get("value")) for x in cjc_unit_options if x.get("value") is not None}
                    for target_label in TEMP_UNIT_ORDER:
                        for unit_val, unit_label in UNIT_LABELS.items():
                            if (target_label.lower() in str(unit_label).lower()) and int(unit_val) not in existing:
                                cjc_unit_options.append({"value": int(unit_val), "label": unit_label})
                                existing.add(int(unit_val))
                                break
                if cjc_unit_options:
                    cjc_unit_options.sort(
                        key=lambda x: (
                            TEMP_UNIT_ORDER.index(next((t for t in TEMP_UNIT_ORDER if t.lower() in str(x.get("label", "")).lower()), TEMP_UNIT_ORDER[0]))
                            if any(t.lower() in str(x.get("label", "")).lower() for t in TEMP_UNIT_ORDER) else 99,
                            str(x.get("label")),
                            int(x.get("value", 999999)),
                        )
                    )
                if current_cjc_unit is not None and all(x.get("value") != int(current_cjc_unit) for x in cjc_unit_options):
                    lbl = UNIT_LABELS.get(int(current_cjc_unit), f"Value {int(current_cjc_unit)}")
                    if _is_temp_unit(lbl):
                        cjc_unit_options.insert(0, {"value": int(current_cjc_unit), "label": lbl})
                if not cjc_unit_options and current_cjc_unit is not None:
                    lbl = UNIT_LABELS.get(int(current_cjc_unit), f"Value {int(current_cjc_unit)}")
                    if _is_temp_unit(lbl):
                        cjc_unit_options = [{"value": int(current_cjc_unit), "label": lbl}]

                current_storage_limit_mode = cached.get("current_storage_limit_mode")
                storage_limit_options = cached.get("storage_limit_options", [])
                try:
                    current_storage_limit_mode = int(node.getStorageLimitMode())
                except Exception as e:
                    log(f"[mscl-web] [{read_tag}] warn node_id={node_id}: getStorageLimitMode failed: {e}")
                try:
                    features = node.features()
                    modes = []
                    try:
                        modes = features.storageLimitModes()
                    except Exception:
                        modes = []
                    opts = []
                    for v in modes:
                        vi = int(v)
                        opts.append({"value": vi, "label": STORAGE_LIMIT_LABELS.get(vi, f"Value {vi}")})
                    if not opts:
                        opts = [{"value": 0, "label": "Overwrite"}, {"value": 1, "label": "Stop"}]
                    storage_limit_options = opts
                except Exception as e:
                    log(f"[mscl-web] [{read_tag}] warn node_id={node_id}: features/storageLimitModes failed: {e}")
                    if not storage_limit_options:
                        storage_limit_options = [{"value": 0, "label": "Overwrite"}, {"value": 1, "label": "Stop"}]
                if current_storage_limit_mode is not None and all(x.get("value") != int(current_storage_limit_mode) for x in storage_limit_options):
                    storage_limit_options.insert(0, {"value": int(current_storage_limit_mode), "label": STORAGE_LIMIT_LABELS.get(int(current_storage_limit_mode), f"Value {int(current_storage_limit_mode)}")})
                if current_storage_limit_mode is None and storage_limit_options:
                    current_storage_limit_mode = int(storage_limit_options[0]["value"])

                current_lost_beacon_timeout = cached.get("current_lost_beacon_timeout")
                try:
                    current_lost_beacon_timeout = int(node.getLostBeaconTimeout())
                except Exception as e:
                    log(f"[mscl-web] [{read_tag}] warn node_id={node_id}: getLostBeaconTimeout failed: {e}")
                if current_lost_beacon_timeout is None:
                    current_lost_beacon_timeout = 2
                current_lost_beacon_enabled = bool(int(current_lost_beacon_timeout) > 0)

                current_diagnostic_interval = cached.get("current_diagnostic_interval")
                try:
                    current_diagnostic_interval = int(node.getDiagnosticInterval())
                except Exception as e:
                    log(f"[mscl-web] [{read_tag}] warn node_id={node_id}: getDiagnosticInterval failed: {e}")
                if current_diagnostic_interval is None:
                    current_diagnostic_interval = 60
                current_diagnostic_enabled = bool(int(current_diagnostic_interval) > 0)

                supports_transducer_type = cached.get("supports_transducer_type")
                supports_temp_sensor_options = cached.get("supports_temp_sensor_options")
                current_transducer_type = cached.get("current_transducer_type")
                current_sensor_type = cached.get("current_sensor_type")
                current_wire_type = cached.get("current_wire_type")
                transducer_options = cached.get("transducer_options", [])
                rtd_sensor_options = cached.get("rtd_sensor_options", [])
                thermistor_sensor_options = cached.get("thermistor_sensor_options", [])
                thermocouple_sensor_options = cached.get("thermocouple_sensor_options", [])
                rtd_wire_options = cached.get("rtd_wire_options", [])

                supports_default_mode = cached.get("supports_default_mode")
                supports_inactivity_timeout = cached.get("supports_inactivity_timeout")
                supports_check_radio_interval = cached.get("supports_check_radio_interval")
                current_default_mode = cached.get("current_default_mode")
                current_inactivity_timeout = cached.get("current_inactivity_timeout")
                current_check_radio_interval = cached.get("current_check_radio_interval")
                default_mode_options = cached.get("default_mode_options", [])

                try:
                    features = node.features()
                except Exception:
                    features = None

                if features is not None:
                    supports_default_mode = _feature_supported(features, "supportsDefaultMode")
                    supports_inactivity_timeout = _feature_supported(features, "supportsInactivityTimeout")
                    supports_check_radio_interval = _feature_supported(features, "supportsCheckRadioInterval")
                    supports_transducer_type = _feature_supported(features, "supportsTransducerType")
                    supports_temp_sensor_options = _feature_supported(features, "supportsTempSensorOptions")

                    # SensorConnect-style behavior: try reads even when supports* says NO.
                    try:
                        current_default_mode = int(node.getDefaultMode())
                        supports_default_mode = True
                    except Exception as e:
                        log(f"[mscl-web] [{read_tag}] warn node_id={node_id}: getDefaultMode failed: {e}")
                    try:
                        modes = []
                        try:
                            modes = features.defaultModes()
                        except Exception:
                            modes = []
                        default_mode_options = []
                        for m in modes:
                            mi = int(m)
                            default_mode_options.append({
                                "value": mi,
                                "label": DEFAULT_MODE_LABELS.get(mi, f"Value {mi}")
                            })
                        default_mode_options = _filter_default_modes(default_mode_options)
                        if default_mode_options:
                            supports_default_mode = True
                        if not default_mode_options:
                            default_mode_options = [
                                {"value": 0, "label": "Idle"},
                                {"value": 5, "label": "Sleep"},
                                {"value": 6, "label": "Sample"},
                            ]
                            default_mode_options = _filter_default_modes(default_mode_options)
                    except Exception as e:
                        log(f"[mscl-web] [{read_tag}] warn node_id={node_id}: features/defaultModes failed: {e}")
                        if not default_mode_options:
                            default_mode_options = [
                                {"value": 0, "label": "Idle"},
                                {"value": 5, "label": "Sleep"},
                                {"value": 6, "label": "Sample"},
                            ]
                        default_mode_options = _filter_default_modes(default_mode_options)

                    try:
                        current_inactivity_timeout = int(node.getInactivityTimeout())
                        supports_inactivity_timeout = True
                    except Exception as e:
                        log(f"[mscl-web] [{read_tag}] warn node_id={node_id}: getInactivityTimeout failed: {e}")
                    try:
                        current_check_radio_interval = int(node.getCheckRadioInterval())
                        supports_check_radio_interval = True
                    except Exception as e:
                        log(f"[mscl-web] [{read_tag}] warn node_id={node_id}: getCheckRadioInterval failed: {e}")

                    try:
                        tr_types = []
                        try:
                            tr_types = features.transducerTypes()
                        except Exception:
                            tr_types = []
                        transducer_options = []
                        for v in tr_types:
                            vi = int(v)
                            transducer_options.append({"value": vi, "label": TRANSDUCER_LABELS.get(vi, f"Value {vi}")})
                        if not transducer_options:
                            transducer_options = [{"value": int(k), "label": v} for k, v in TRANSDUCER_LABELS.items()]
                    except Exception as e:
                        log(f"[mscl-web] [{read_tag}] warn node_id={node_id}: features/transducerTypes failed: {e}")
                        if not transducer_options:
                            transducer_options = [{"value": int(k), "label": v} for k, v in TRANSDUCER_LABELS.items()]
                    try:
                        tc_types = []
                        try:
                            tc_types = features.thermocoupleTypes()
                        except Exception:
                            tc_types = []
                        thermocouple_sensor_options = []
                        for v in tc_types:
                            vi = int(v)
                            thermocouple_sensor_options.append({"value": vi, "label": THERMOCOUPLE_SENSOR_LABELS.get(vi, f"Value {vi}")})
                    except Exception as e:
                        log(f"[mscl-web] [{read_tag}] warn node_id={node_id}: features/thermocoupleTypes failed: {e}")

                # SensorConnect-style behavior: read current temp sensor options even if supports* says NO.
                tso, tso_err = _get_temp_sensor_options(node)
                if tso is not None:
                    try:
                        current_transducer_type = int(tso.transducerType())
                        supports_transducer_type = True
                    except Exception:
                        pass
                    try:
                        if current_transducer_type == _wt("transducer_rtd", 1):
                            current_sensor_type = int(tso.rtdType())
                        elif current_transducer_type == _wt("transducer_thermistor", 2):
                            current_sensor_type = int(tso.thermistorType())
                        elif current_transducer_type == _wt("transducer_thermocouple", 0):
                            current_sensor_type = int(tso.thermocoupleType())
                    except Exception:
                        pass
                    try:
                        current_wire_type = int(tso.rtdWireType())
                    except Exception:
                        pass
                    supports_temp_sensor_options = True
                elif tso_err:
                    log(f"[mscl-web] [{read_tag}] warn node_id={node_id}: getTempSensorOptions failed: {tso_err}")

                if not transducer_options:
                    transducer_options = [{"value": int(k), "label": v} for k, v in TRANSDUCER_LABELS.items()]
                if current_transducer_type is not None and all(x.get("value") != int(current_transducer_type) for x in transducer_options):
                    transducer_options.insert(0, {"value": int(current_transducer_type), "label": TRANSDUCER_LABELS.get(int(current_transducer_type), f"Value {int(current_transducer_type)}")})
                if current_transducer_type is None and transducer_options:
                    current_transducer_type = int(transducer_options[0]["value"])

                rtd_sensor_options = [{"value": int(k), "label": v} for k, v in RTD_SENSOR_LABELS.items()]
                thermistor_sensor_options = [{"value": int(k), "label": v} for k, v in THERMISTOR_SENSOR_LABELS.items()]
                if not thermocouple_sensor_options:
                    thermocouple_sensor_options = [{"value": int(k), "label": v} for k, v in THERMOCOUPLE_SENSOR_LABELS.items()]
                if current_transducer_type == _wt("transducer_thermocouple", 0) and current_sensor_type is not None:
                    if all(x.get("value") != int(current_sensor_type) for x in thermocouple_sensor_options):
                        thermocouple_sensor_options.insert(0, {"value": int(current_sensor_type), "label": THERMOCOUPLE_SENSOR_LABELS.get(int(current_sensor_type), f"Value {int(current_sensor_type)}")})
                rtd_wire_options = [{"value": int(k), "label": v} for k, v in RTD_WIRE_LABELS.items()]
                default_mode_options = filter_default_modes_for_model(
                    model,
                    default_mode_options,
                    DEFAULT_MODE_LABELS,
                    current_default_mode,
                )
                tx_power_options = tx_power_options_for_model(model, current_power)

                # Rates (when available)
                supported_rates = cached.get("supported_rates", [])
                if (refresh_eeprom or not supported_rates) and current_rate is not None:
                    supported_rates = [{"enum_val": int(current_rate), "str_val": _sample_rate_label(current_rate)}]
                    try:
                        features = node.features()
                        rates = features.sampleRates(mscl.WirelessTypes.samplingMode_sync, 1, 0)
                        supported_rates = []
                        for r in rates:
                            rid = int(r)
                            supported_rates.append({"enum_val": rid, "str_val": _sample_rate_label(rid, r)})
                    except Exception as e:
                        log(f"[mscl-web] [{read_tag}] warn node_id={node_id}: features/sampleRates failed: {e}")
                supported_rates = _filter_sample_rates_for_model(model, supported_rates, current_rate)
            
                channels = []
                if active_mask is not None:
                    for i in range(1, 3):
                        channels.append({"id": i, "enabled": active_mask.enabled(i)})
                elif isinstance(cached.get("channels"), list) and cached.get("channels"):
                    channels = cached.get("channels")
                else:
                    channels = [{"id": 1, "enabled": True}, {"id": 2, "enabled": False}]
                current_inactivity_enabled = bool((current_inactivity_timeout is not None) and (int(current_inactivity_timeout) > 0))
        
                payload = dict(
                    success=True, model=model, sn=sn, fw=fw,
                    region=region, last_comm=last_comm, state=node_state, state_text=state_text,
                    node_address=node_address, frequency=frequency,
                    storage_pct=storage_pct, storage_capacity_raw=storage_capacity_raw, sampling_mode=sampling_mode, sampling_mode_raw=sampling_mode_raw,
                    current_data_mode=current_data_mode, data_mode_text=data_mode_text, data_mode_options=data_mode_options,
                    current_collection_method=current_collection_method,
                    current_collection_method_text=current_collection_method_text,
                    current_data_format=current_data_format, current_data_type=current_data_type,
                    current_input_range=current_input_range, supported_input_ranges=supported_input_ranges,
                    current_unit=current_unit, unit_options=unit_options,
                    current_cjc_unit=current_cjc_unit, cjc_unit_options=cjc_unit_options,
                    current_rate=current_rate, current_power=current_power, current_power_enum=current_power_enum,
                    tx_power_options=tx_power_options,
                    comm_protocol=comm_protocol, comm_protocol_text=comm_protocol_text,
                    supported_rates=supported_rates, channels=channels,
                    current_low_pass=current_low_pass, low_pass_options=low_pass_options,
                    current_storage_limit_mode=current_storage_limit_mode, storage_limit_options=storage_limit_options,
                    current_lost_beacon_timeout=current_lost_beacon_timeout,
                    current_lost_beacon_enabled=current_lost_beacon_enabled,
                    current_diagnostic_interval=current_diagnostic_interval,
                    current_diagnostic_enabled=current_diagnostic_enabled,
                    supports_default_mode=bool(supports_default_mode),
                    supports_inactivity_timeout=bool(supports_inactivity_timeout),
                    supports_check_radio_interval=bool(supports_check_radio_interval),
                    supports_transducer_type=bool(supports_transducer_type),
                    supports_temp_sensor_options=bool(supports_temp_sensor_options),
                    current_default_mode=current_default_mode,
                    current_inactivity_timeout=current_inactivity_timeout,
                    current_inactivity_enabled=current_inactivity_enabled,
                    current_check_radio_interval=current_check_radio_interval,
                    default_mode_options=default_mode_options,
                    current_transducer_type=current_transducer_type,
                    current_sensor_type=current_sensor_type,
                    current_wire_type=current_wire_type,
                    transducer_options=transducer_options,
                    rtd_sensor_options=rtd_sensor_options,
                    thermistor_sensor_options=thermistor_sensor_options,
                    thermocouple_sensor_options=thermocouple_sensor_options,
                    rtd_wire_options=rtd_wire_options,
                )
                state.NODE_READ_CACHE[node_id] = dict(payload, ts=time.time())
                log(f"[mscl-web] [{read_tag}] success node_id={node_id} sample_rate={payload.get('current_rate')} fw={payload.get('fw')}")
                return jsonify(**payload)
            except Exception as e:
                last_err = str(e)
                log(f"[mscl-web] [{read_tag}] error node_id={node_id}: {e}")
                if "EEPROM" in last_err:
                    metric_inc("eeprom_retries_read")
                    backoff = min(4.0, 0.5 * (2 ** (attempt - 1)))
                    time.sleep(backoff)
                    continue
                mark_base_disconnected()
                time.sleep(0.5)
                continue
    if last_err:
        log(f"[mscl-web] [{read_tag}] failed node_id={node_id}: {last_err}")
    else:
        log(f"[mscl-web] [{read_tag}] failed node_id={node_id}: Read failed")
    return jsonify(success=False, error=last_err or "Read failed")

@app.route('/api/probe/<int:node_id>')
def api_probe(node_id):
    log(f"[mscl-web] Probe request node_id={node_id}")
    with state.OP_LOCK:
        ok, msg = internal_connect()
        if not ok or state.BASE_STATION is None:
            err = f"Base station not connected: {msg}"
            log(f"[mscl-web] Probe failed: {err}")
            return jsonify(success=False, error=err)
        try:
            if _is_sampling_active(node_id):
                return jsonify(success=False, error="Sampling active. Stop sampling before probe.")
            ensure_beacon_on()
            node = mscl.WirelessNode(node_id, state.BASE_STATION)
            node.readWriteRetries(10)
            # Try to nudge node without touching EEPROM
            try:
                node.setToIdle()
                time.sleep(1.5)
            except Exception as e:
                log(f"[mscl-web] Probe setToIdle failed node_id={node_id}: {e}")
            try:
                node.ping()
            except Exception as e:
                log(f"[mscl-web] Probe ping failed node_id={node_id}: {e}")
            # Skip resendStartSyncSampling to avoid EEPROM reads on stuck nodes
            # Final idle
            try:
                node.setToIdle()
                time.sleep(1.0)
            except Exception:
                pass
            return jsonify(success=True, message="Probe commands sent")
        except Exception as e:
            log(f"[mscl-web] Probe error node_id={node_id}: {e}")
            return jsonify(success=False, error=str(e))

@app.route('/api/node_idle/<int:node_id>', methods=['POST'])
def api_node_idle(node_id):
    with state.OP_LOCK:
        if node_id in state.IDLE_IN_PROGRESS:
            return jsonify(success=False, error="Set to Idle already in progress")
        if _is_sampling_active(node_id):
            return jsonify(success=False, error="Sampling active. Stop sampling before Set to Idle.")
        state.IDLE_IN_PROGRESS.add(node_id)
        ok, msg = internal_connect()
        if not ok or state.BASE_STATION is None:
            state.IDLE_IN_PROGRESS.discard(node_id)
            return jsonify(success=False, error=f"Base station not connected: {msg}")
        try:
            ensure_beacon_on()
            node = mscl.WirelessNode(node_id, state.BASE_STATION)
            node.readWriteRetries(10)
            idle_status = send_idle_sensorconnect_style(node, node_id, "manual-idle")
            sent = bool(idle_status.get("command_sent"))
            confirmed = bool(idle_status.get("state_confirmed"))
            reason = idle_status.get("reason") or "unknown"
            idle_result = idle_status.get("idle_result") or ("success" if confirmed else "pending")
            if not sent:
                log(f"[mscl-web] [PREP-IDLE] failed node_id={node_id} reason={reason}")
                return jsonify(success=False, error=reason, idle_confirmed=False, idle_result=idle_result, idle_status=idle_status)
            if confirmed:
                log(f"[mscl-web] [PREP-IDLE] success node_id={node_id}")
                return jsonify(success=True, message="Node set to Idle", idle_confirmed=True, idle_result=idle_result, reason=reason, idle_status=idle_status)
            log(f"[mscl-web] [PREP-IDLE] pending node_id={node_id} reason={reason}")
            return jsonify(success=True, message="Idle command sent", idle_confirmed=False, idle_result=idle_result, reason=reason, idle_status=idle_status)
        except Exception as e:
            log(f"[mscl-web] [PREP-IDLE] failed node_id={node_id}: {e}")
            return jsonify(success=False, error=str(e))
        finally:
            state.IDLE_IN_PROGRESS.discard(node_id)

@app.route('/api/node_cycle_power/<int:node_id>', methods=['POST'])
def api_node_cycle_power(node_id):
    with state.OP_LOCK:
        ok, msg = internal_connect()
        if not ok or state.BASE_STATION is None:
            return jsonify(success=False, error=f"Base station not connected: {msg}")
        try:
            ensure_beacon_on()
            node = mscl.WirelessNode(node_id, state.BASE_STATION)
            node.readWriteRetries(10)
            node.cyclePower()
            log(f"[mscl-web] [PREP-CYCLE] success node_id={node_id}")
            return jsonify(success=True, message="Power cycle command sent")
        except Exception as e:
            log(f"[mscl-web] [PREP-CYCLE] failed node_id={node_id}: {e}")
            return jsonify(success=False, error=str(e))

@app.route('/api/node_sampling/<int:node_id>', methods=['POST'])
def api_node_sampling(node_id):
    with state.OP_LOCK:
        body = request.json or {}
        # Backward compatibility path (old UI sent only duration_sec).
        if "duration_sec" in body:
            body = {
                "duration_value": int(body.get("duration_sec", 0) or 0),
                "duration_units": "seconds",
                "continuous": int(body.get("duration_sec", 0) or 0) <= 0,
                "log_transmit_mode": "transmit",
                "data_type": "float",
                "sample_rate": None,
            }
        res = _start_sampling_run(node_id, body)
        if not res.get("success"):
            return jsonify(success=False, error=res.get("error", "Sampling start failed"))
        run = res["run"]
        return jsonify(success=True, message=f"Sampling started ({run['start_method']})", run=run)


@app.route('/api/sampling/start/<int:node_id>', methods=['POST'])
def api_sampling_start(node_id):
    with state.OP_LOCK:
        body = request.json or {}
        res = _start_sampling_run(node_id, body)
        if not res.get("success"):
            return jsonify(success=False, error=res.get("error", "Sampling start failed"))
        return jsonify(success=True, run=res["run"])


@app.route('/api/sampling/group_start', methods=['POST'])
def api_sampling_group_start():
    with state.OP_LOCK:
        body = request.json or {}
        res = _start_sampling_group_run(body)
        if not res.get("success"):
            return jsonify(success=False, error=res.get("error", "Group sampling start failed"))
        return jsonify(success=True, run=res["run"])


@app.route('/api/sampling/stop/<int:node_id>', methods=['POST'])
def api_sampling_stop(node_id):
    with state.OP_LOCK:
        ok, msg = internal_connect()
        if not ok or state.BASE_STATION is None:
            return jsonify(success=False, error=f"Base station not connected: {msg}")
        try:
            ensure_beacon_on()
            node = mscl.WirelessNode(node_id, state.BASE_STATION)
            node.readWriteRetries(10)
            idle_status = send_idle_sensorconnect_style(node, node_id, "stop-sampling")
            state.SAMPLE_STOP_TOKENS[node_id] = time.time()
            run = state.SAMPLE_RUNS.get(node_id, {})
            run.update({
                "state": "stopped",
                "stopped_at": int(time.time()),
                "idle_result": idle_status.get("idle_result"),
            })
            state.SAMPLE_RUNS[node_id] = run
            if idle_status.get("state_confirmed"):
                log(f"[mscl-web] [S-RUN] stop ok node_id={node_id}")
                return jsonify(success=True, message="Sampling stopped", idle_status=idle_status, run=run)
            reason = idle_status.get("reason", "pending")
            log(f"[mscl-web] [S-RUN] stop pending node_id={node_id}: {reason}")
            return jsonify(success=True, message=f"Stop sent ({reason})", idle_status=idle_status, run=run)
        except Exception as e:
            log(f"[mscl-web] [S-RUN] stop failed node_id={node_id}: {e}")
            return jsonify(success=False, error=str(e))


@app.route('/api/sampling/group_stop', methods=['POST'])
def api_sampling_group_stop():
    with state.OP_LOCK:
        ok, msg = internal_connect()
        if not ok or state.BASE_STATION is None:
            return jsonify(success=False, error=f"Base station not connected: {msg}")
        ensure_beacon_on()
        results = []
        any_success = False
        for node_id in MSCL_NODE_IDS:
            try:
                node = mscl.WirelessNode(int(node_id), state.BASE_STATION)
                node.readWriteRetries(10)
                idle_status = send_idle_sensorconnect_style(node, int(node_id), "stop-sampling-group")
                state.SAMPLE_STOP_TOKENS[int(node_id)] = time.time()
                run = state.SAMPLE_RUNS.get(int(node_id), {})
                run.update({
                    "state": "stopped",
                    "stopped_at": int(time.time()),
                    "idle_result": idle_status.get("idle_result"),
                })
                state.SAMPLE_RUNS[int(node_id)] = run
                results.append({"node_id": int(node_id), "success": True, "idle_status": idle_status})
                any_success = True
            except Exception as e:
                log(f"[mscl-web] [S-GROUP] stop failed node_id={node_id}: {e}")
                results.append({"node_id": int(node_id), "success": False, "error": str(e)})
        return jsonify(success=any_success, results=results, node_ids=MSCL_NODE_IDS)


@app.route('/api/sampling/status/<int:node_id>')
def api_sampling_status(node_id):
    with state.OP_LOCK:
        state_num = None
        state_text = "Unknown"
        freshness_reason = None
        link_state = "offline"
        ok, msg = internal_connect(force_ping=False)
        if ok and state.BASE_STATION is not None:
            try:
                node = mscl.WirelessNode(node_id, state.BASE_STATION)
                node.readWriteRetries(5)
                state_num, state_text, freshness_reason = _node_state_info(node)
                link_state = "ok"
            except Exception as e:
                link_state = f"degraded: {e}"
        else:
            link_state = f"offline: {msg}"

        run = state.SAMPLE_RUNS.get(node_id, {})
        now = int(time.time())
        duration_sec = int(run.get("duration_sec") or 0)
        started_at = int(run.get("started_at") or 0)
        time_left = None
        if duration_sec > 0 and started_at > 0:
            time_left = max(0, duration_sec - max(0, now - started_at))
        return jsonify(
            success=True,
            node_id=node_id,
            node_state=state_text,
            node_state_num=state_num,
            freshness_reason=freshness_reason,
            link_state=link_state,
            run=run,
            time_left_sec=time_left,
        )


@app.route('/api/sampling/group_status')
def api_sampling_group_status():
    with state.OP_LOCK:
        items = []
        any_running = False
        for node_id in MSCL_NODE_IDS:
            state_num = None
            state_text = "Unknown"
            freshness_reason = None
            link_state = "offline"
            ok, msg = internal_connect(force_ping=False)
            if ok and state.BASE_STATION is not None:
                try:
                    node = mscl.WirelessNode(int(node_id), state.BASE_STATION)
                    node.readWriteRetries(5)
                    state_num, state_text, freshness_reason = _node_state_info(node)
                    link_state = "ok"
                except Exception as e:
                    link_state = f"degraded: {e}"
            else:
                link_state = f"offline: {msg}"

            run = state.SAMPLE_RUNS.get(int(node_id), {})
            if run.get("state") == "running":
                any_running = True
            items.append(
                {
                    "node_id": int(node_id),
                    "node_state": state_text,
                    "node_state_num": state_num,
                    "freshness_reason": freshness_reason,
                    "link_state": link_state,
                    "run": run,
                }
            )
        return jsonify(success=True, node_ids=MSCL_NODE_IDS, any_running=any_running, items=items)

@app.route('/api/node_sleep/<int:node_id>', methods=['POST'])
def api_node_sleep(node_id):
    with state.OP_LOCK:
        ok, msg = internal_connect()
        if not ok or state.BASE_STATION is None:
            return jsonify(success=False, error=f"Base station not connected: {msg}")
        try:
            ensure_beacon_on()
            node = mscl.WirelessNode(node_id, state.BASE_STATION)
            node.readWriteRetries(10)
            node.sleep()
            log(f"[mscl-web] [SLEEP] sleep command sent node_id={node_id}")
            return jsonify(success=True, message="Sleep command sent")
        except Exception as e:
            log(f"[mscl-web] [SLEEP] failed node_id={node_id}: {e}")
            return jsonify(success=False, error=str(e))

@app.route('/api/clear_storage/<int:node_id>', methods=['POST'])
def api_clear_storage(node_id):
    with state.OP_LOCK:
        ok, msg = internal_connect()
        if not ok or state.BASE_STATION is None:
            return jsonify(success=False, error=f"Base station not connected: {msg}")
        try:
            ensure_beacon_on()
            node = mscl.WirelessNode(node_id, state.BASE_STATION)
            node.readWriteRetries(15)
            set_idle_with_retry(node, node_id, "before-clear-storage", attempts=2, delay_sec=0.8, required=False)
            node.erase()
            set_idle_with_retry(node, node_id, "after-clear-storage", attempts=2, delay_sec=0.8, required=False)
            cached = state.NODE_READ_CACHE.get(node_id, {})
            cached["storage_pct"] = 0.0
            cached["ts"] = time.time()
            state.NODE_READ_CACHE[node_id] = cached
            log(f"[mscl-web] [CLEAR-STORAGE] success node_id={node_id}")
            return jsonify(success=True, message="Storage cleared")
        except Exception as e:
            log(f"[mscl-web] [CLEAR-STORAGE] failed node_id={node_id}: {e}")
            return jsonify(success=False, error=str(e))


@app.route('/api/export_storage/<int:node_id>')
def api_export_storage(node_id):
    try:
        req = parse_export_storage_request(request.args, _parse_iso_utc_to_ns)
    except ExportRequestValidationError as ve:
        return jsonify(success=False, error=str(ve)), int(getattr(ve, "status_code", 400))

    export_format = req["export_format"]
    ingest_influx = req["ingest_influx"]
    ui_from_raw = req["ui_from_raw"]
    ui_to_raw = req["ui_to_raw"]
    ui_window_from_ns = req["ui_window_from_ns"]
    ui_window_to_ns = req["ui_window_to_ns"]
    host_hours = req["host_hours"]

    with state.OP_LOCK:
        ok, msg = internal_connect()
        if not ok or state.BASE_STATION is None:
            return jsonify(success=False, error=f"Base station not connected: {msg}"), 503
        try:
            return execute_export_storage_connected(
                node_id=int(node_id),
                export_format=export_format,
                ingest_influx=ingest_influx,
                ui_from_raw=ui_from_raw,
                ui_to_raw=ui_to_raw,
                ui_window_from_ns=ui_window_from_ns,
                ui_window_to_ns=ui_window_to_ns,
                host_hours=host_hours,
                state_module=state,
                mscl_mod=mscl,
                ensure_beacon_on_fn=ensure_beacon_on,
                pause_stream_reader_fn=_pause_stream_reader,
                send_idle_sensorconnect_style_fn=send_idle_sensorconnect_style,
                coerce_logged_sweeps_fn=_coerce_logged_sweeps,
                logged_sweep_rows_fn=_logged_sweep_rows,
                resolve_export_time_window_fn=resolve_export_time_window,
                filter_rows_by_host_window_fn=filter_rows_by_host_window,
                backfill_rows_to_influx_stream_fn=_backfill_rows_to_influx_stream,
                metric_inc_fn=metric_inc,
                log_func=log,
                source_node_export=MSCL_SOURCE_NODE_EXPORT,
                jsonify_fn=jsonify,
                response_cls=Response,
                send_file_fn=send_file,
            )
        except Exception as e:
            err = str(e)
            status_code, mapped_error = map_export_storage_error(err)
            if int(status_code) == 409:
                log(f"[mscl-web] [EXPORT-STORAGE] failed node_id={node_id}: {err} | hint={mapped_error}")
            else:
                log(f"[mscl-web] [EXPORT-STORAGE] failed node_id={node_id}: {err}")
            return jsonify(success=False, error=mapped_error), int(status_code)


@app.route('/api/write', methods=['POST'])
def api_write():
    data = request.json
    raw_node_id = data.get('node_id') if isinstance(data, dict) else None
    log(f"[mscl-web] Write request node_id={raw_node_id}")
    with state.OP_LOCK:
        cached0 = cached_node_snapshot(raw_node_id, state.NODE_READ_CACHE)
        try:
            node_id, _ = validate_write_request(data, cached0)
        except WriteRequestValidationError as ve:
            return jsonify(success=False, error=str(ve)), int(getattr(ve, "status_code", 400))

        def _connected_attempt():
            return apply_write_connected(
                node_id=int(node_id),
                data=data,
                base_station=state.BASE_STATION,
                node_read_cache=state.NODE_READ_CACHE,
                ensure_beacon_on_fn=ensure_beacon_on,
                mscl_mod=mscl,
                normalize_write_payload_fn=normalize_write_payload,
                normalize_tx_power_fn=normalize_tx_power,
                is_tc_link_200_model_fn=is_tc_link_200_oem_model,
                feature_supported_fn=_feature_supported,
                build_write_config_fn=build_write_config,
                update_write_cache_fn=update_write_cache,
                ch1_mask_fn=ch1_mask,
                ch2_mask_fn=ch2_mask,
                get_temp_sensor_options_fn=_get_temp_sensor_options,
                set_temp_sensor_options_fn=_set_temp_sensor_options,
                wt_fn=_wt,
                data_mode_labels=DATA_MODE_LABELS,
                unit_labels=UNIT_LABELS,
                log_func=log,
                now_ts_fn=time.time,
                jsonify_fn=jsonify,
            )

        res = run_write_retry_loop(
            node_id=int(node_id),
            max_attempts=5,
            log_func=log,
            sleep_fn=time.sleep,
            internal_connect_fn=internal_connect,
            base_connected_fn=lambda: state.BASE_STATION is not None,
            connected_attempt_fn=_connected_attempt,
            metric_inc_fn=metric_inc,
            mark_base_disconnected_fn=mark_base_disconnected,
        )
        if res.get("response") is not None:
            return res.get("response")
        return jsonify(success=False, error=res.get("error") or "Write failed")

def run_config_server():
    _start_streamer()
    app.run(host='0.0.0.0', port=5000)


if __name__ == "__main__":
    run_config_server()
