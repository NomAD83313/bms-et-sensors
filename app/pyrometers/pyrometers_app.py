from __future__ import annotations

import os
import select
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, request
from influxdb_client import InfluxDBClient, Point, WritePrecision  # type: ignore
from influxdb_client.client.write_api import WriteOptions  # type: ignore
import serial  # type: ignore

from pyrometer_profiles import (
    STREAM_FRAME_FORMAT_BURST_WORDS,
    STREAM_FRAME_FORMAT_MARKED_BURST_WORDS,
    DeviceProfile,
    build_device_profiles,
    preferred_device_id,
    summarize_devices_status,
)
from pyrometers_config import (
    LOGGING_PRESETS_HZ,
    THERMOMETER_PROTOCOL,
    load_config,
    logging_interval_ms,
    normalize_logging_hz,
)
from pyrometers_protocol import (
    build_classic_ct_burst_start_commands,
    build_classic_ct_burst_stop_commands,
    build_optris_burst_start_commands,
    build_optris_burst_stop_commands,
    build_optris_read_emissivity_command,
    build_optris_read_transmissivity_command,
    build_optris_set_ambient_fixed_temperature_command,
    build_optris_set_ambient_source_command,
    build_optris_set_emissivity_command,
    build_optris_set_transmissivity_command,
    build_read_serial_number_command,
    normalize_hex_commands,
    parse_binary_frame,
    parse_binary_frames,
    parse_burst_word_frames,
    parse_marked_burst_word_frames,
    parse_poll_response,
    parse_serial_number_response,
)
from pyrometers_stream_state import stream_is_stale

app = Flask(__name__)

CONFIG = load_config()
THERMOMETER_BAUD = CONFIG.baud
THERMOMETER_TIMEOUT_MS = CONFIG.timeout_ms
THERMOMETER_POLL_SEC = CONFIG.poll_sec
THERMOMETER_STREAM_TIMEOUT_MS = CONFIG.stream_timeout_ms
THERMOMETER_STREAM_STALE_SEC = CONFIG.stream_stale_sec
THERMOMETER_OPTRIS_STALE_REOPEN_SEC = float(os.getenv("PYROMETERS_OPTRIS_STALE_REOPEN_SEC", "20.0"))
THERMOMETER_EMPTY_STREAM_SLEEP_SEC = CONFIG.empty_stream_sleep_sec
THERMOMETER_STREAM_CHUNK_SIZE = CONFIG.stream_chunk_size
THERMOMETER_LOG_HZ = CONFIG.log_hz
THERMOMETER_STARTUP_DISCARD_SAMPLES = CONFIG.startup_discard_samples
THERMOMETER_INFLUX_BATCH_SIZE = CONFIG.influx_batch_size
THERMOMETER_INFLUX_FLUSH_MS = CONFIG.influx_flush_ms
THERMOMETER_MEASUREMENT = CONFIG.measurement
THERMOMETER_HTTP_PORT = CONFIG.http_port
INFLUX_URL = CONFIG.influx_url
INFLUX_TOKEN = CONFIG.influx_token
INFLUX_ORG = CONFIG.influx_org
INFLUX_BUCKET = CONFIG.influx_bucket

STATE_LOCK = threading.Lock()
POLL_SERIAL_BUS_LOCK = threading.Lock()
POLL_STOP = threading.Event()
DEVICE_PROFILES = build_device_profiles()


@dataclass
class DeviceRuntime:
    profile: DeviceProfile
    serial_lock: threading.Lock = field(default_factory=threading.Lock)
    command_lock: threading.Lock = field(default_factory=threading.Lock)
    ir_setting_requests: deque["IrSettingRequest"] = field(default_factory=deque)
    frame_timestamps: deque[float] = field(default_factory=lambda: deque(maxlen=4096))
    frame_metrics_lock: threading.Lock = field(default_factory=threading.Lock)
    log_timestamps: deque[float] = field(default_factory=lambda: deque(maxlen=4096))
    log_metrics_lock: threading.Lock = field(default_factory=threading.Lock)
    last_batch_monotonic: float | None = None
    last_frame_monotonic: float | None = None
    stream_opened_monotonic: float | None = None
    stale_started_monotonic: float | None = None
    last_stale_reopen_monotonic: float | None = None
    next_log_due_ts: float | None = None
    startup_discard_remaining: int = 0
    consecutive_poll_misses: int = 0


@dataclass
class IrSettingRequest:
    values: dict[str, Any]
    done: threading.Event = field(default_factory=threading.Event)
    result: dict[str, Any] = field(default_factory=dict)


RUNTIMES = {profile.id: DeviceRuntime(profile) for profile in DEVICE_PROFILES}

DEVICE_STATES: dict[str, dict[str, Any]] = {
    profile.id: {
        "id": profile.id,
        "status": "starting",
        "port": profile.default_port,
        "device_name": profile.device_name,
        "display_name": profile.display_name,
        "ui_subtitle": profile.ui_subtitle,
        "source_tag": profile.source_tag,
        "expected_sensor_serial": profile.sensor_serial or None,
        "detected_sensor_serial": None,
        "sensor_serial_verified": None,
        "port_present": False,
        "connected": False,
        "last_measurement_c": None,
        "last_measurement_raw": None,
        "last_measurement_at": None,
        "last_status_text": None,
        "last_binary_frame_hex": None,
        "last_measurement_payload": None,
        "sensor_head_temperature_c": None,
        "controller_box_temperature_c": None,
        "object_temperature_c": None,
        "temperature_labels": {
            "sensor_head_temperature_c": "THead",
            "controller_box_temperature_c": "TBox",
            "object_temperature_c": "TObj",
        },
        "emissivity": None,
        "transmissivity": None,
        "ambient_compensation_source": None,
        "ambient_compensation_fixed_c": None,
        "last_ir_settings_at": None,
        "protocol_mode": THERMOMETER_PROTOCOL,
        "device_mode": profile.mode,
        "stream_frame_format": profile.stream_frame_format,
        "burst_interval_ms": profile.burst_interval_ms if profile.burst_mode else None,
        "burst_channels": list(profile.burst_channels) if profile.burst_mode else [],
        "checksum_enabled": profile.checksum_enabled,
        "last_error": None,
        "serial_reopen_count": 0,
        "stream_stale_count": 0,
        "last_stream_stale_at": None,
        "poll_interval_sec": THERMOMETER_POLL_SEC,
        "stream_timeout_ms": THERMOMETER_STREAM_TIMEOUT_MS,
        "stream_stale_sec": THERMOMETER_STREAM_STALE_SEC,
        "optris_stale_reopen_sec": THERMOMETER_OPTRIS_STALE_REOPEN_SEC,
        "logging_target_hz": THERMOMETER_LOG_HZ,
        "logging_interval_ms": round(1000.0 / THERMOMETER_LOG_HZ, 3) if THERMOMETER_LOG_HZ > 0 else 0.0,
        "startup_discard_samples": THERMOMETER_STARTUP_DISCARD_SAMPLES,
        "startup_discard_remaining": 0,
        "influx_batch_size": THERMOMETER_INFLUX_BATCH_SIZE,
        "influx_flush_ms": THERMOMETER_INFLUX_FLUSH_MS,
        "frames_total": 0,
        "frames_last_1s": 0,
        "frames_per_sec": 0.0,
        "frames_last_10s": 0,
        "effective_log_hz": 0.0,
        "peak_log_hz": 0.0,
        "logged_samples_total": 0,
        "logged_samples_last_1s": 0,
        "logged_samples_last_10s": 0,
        "effective_logged_hz": 0.0,
        "peak_logged_hz": 0.0,
        "last_chunk_bytes": 0,
        "baudrate": profile.baud,
    }
    for profile in DEVICE_PROFILES
}


def _influx_enabled() -> bool:
    return bool(INFLUX_TOKEN and INFLUX_ORG and INFLUX_BUCKET)


def _update_device_state(device_id: str, **kwargs: Any) -> dict[str, Any]:
    with STATE_LOCK:
        DEVICE_STATES[device_id].update(kwargs)
        return dict(DEVICE_STATES[device_id])


def _get_device_state(device_id: str) -> dict[str, Any]:
    with STATE_LOCK:
        return dict(DEVICE_STATES[device_id])


def _get_devices_state() -> dict[str, dict[str, Any]]:
    with STATE_LOCK:
        return {device_id: dict(state) for device_id, state in DEVICE_STATES.items()}


def _app_health_payload() -> dict[str, Any]:
    devices = _get_devices_state()
    selected_id = preferred_device_id(devices)
    connected_ids = [device_id for device_id, item in devices.items() if bool(item.get("connected"))]
    present_ids = [device_id for device_id, item in devices.items() if bool(item.get("port_present"))]
    return {
        "status": summarize_devices_status(devices),
        "display_name": "Pyrometers",
        "selected_device_id": selected_id,
        "connected_devices": connected_ids,
        "present_devices": present_ids,
        "devices": devices,
    }


def _normalize_logging_hz(value: Any) -> float:
    return normalize_logging_hz(value, THERMOMETER_LOG_HZ)


def _logging_interval_ms(hz: float) -> float:
    return logging_interval_ms(hz)


def _set_logging_target_hz(value: Any) -> dict[str, Any]:
    hz = _normalize_logging_hz(value)
    interval_ms = _logging_interval_ms(hz)
    with STATE_LOCK:
        for state in DEVICE_STATES.values():
            state["logging_target_hz"] = hz
            state["logging_interval_ms"] = interval_ms
    for runtime in RUNTIMES.values():
        runtime.next_log_due_ts = None
    return _app_health_payload()


def _serial_candidates(profile: DeviceProfile) -> list[str]:
    candidates: list[str] = []
    configured = os.getenv(profile.port_env_key, "").strip()
    if configured:
        candidates.append(configured)
    candidates.append(profile.default_port)
    by_id = "/dev/serial/by-id"
    if os.path.isdir(by_id):
        for name in sorted(os.listdir(by_id)):
            raw = os.path.join(by_id, name)
            lowered = name.lower()
            if any(token in lowered for token in profile.match_tokens):
                candidates.append(raw)
    seen: set[str] = set()
    ordered: list[str] = []
    for item in candidates:
        if item and item not in seen:
            seen.add(item)
            ordered.append(item)
    return ordered


def _resolve_port(profile: DeviceProfile) -> str:
    for path in _serial_candidates(profile):
        if os.path.exists(path):
            return path
    configured = os.getenv(profile.port_env_key, "").strip()
    if configured:
        return configured
    return profile.default_port


def _record_frame_metrics(runtime: DeviceRuntime, observed_ts: float, chunk_bytes: int) -> dict[str, Any]:
    with runtime.frame_metrics_lock:
        runtime.frame_timestamps.append(observed_ts)
        cutoff_1s = observed_ts - 1.0
        cutoff_10s = observed_ts - 10.0
        frames_last_1s = sum(1 for ts in runtime.frame_timestamps if ts >= cutoff_1s)
        while runtime.frame_timestamps and runtime.frame_timestamps[0] < cutoff_10s:
            runtime.frame_timestamps.popleft()
        frames_last_10s = len(runtime.frame_timestamps)
        effective_hz = frames_last_10s / 10.0
    state = _get_device_state(runtime.profile.id)
    frames_total = int(state.get("frames_total") or 0) + 1
    peak_log_hz = max(float(state.get("peak_log_hz") or 0.0), effective_hz)
    metrics = {
        "frames_total": frames_total,
        "frames_last_1s": frames_last_1s,
        "frames_per_sec": round(float(frames_last_1s), 3),
        "frames_last_10s": frames_last_10s,
        "effective_log_hz": round(effective_hz, 3),
        "peak_log_hz": round(peak_log_hz, 3),
        "last_chunk_bytes": int(chunk_bytes),
    }
    _update_device_state(runtime.profile.id, **metrics)
    return metrics


def _record_frame_batch_metrics(
    runtime: DeviceRuntime, observed_timestamps: list[float], chunk_bytes: int
) -> dict[str, Any]:
    if not observed_timestamps:
        return {}
    observed_ts = observed_timestamps[-1]
    with runtime.frame_metrics_lock:
        runtime.frame_timestamps.extend(observed_timestamps)
        cutoff_1s = observed_ts - 1.0
        cutoff_10s = observed_ts - 10.0
        frames_last_1s = sum(1 for ts in runtime.frame_timestamps if ts >= cutoff_1s)
        while runtime.frame_timestamps and runtime.frame_timestamps[0] < cutoff_10s:
            runtime.frame_timestamps.popleft()
        frames_last_10s = len(runtime.frame_timestamps)
        effective_hz = frames_last_10s / 10.0
    state = _get_device_state(runtime.profile.id)
    frames_total = int(state.get("frames_total") or 0) + len(observed_timestamps)
    peak_log_hz = max(float(state.get("peak_log_hz") or 0.0), effective_hz)
    metrics = {
        "frames_total": frames_total,
        "frames_last_1s": frames_last_1s,
        "frames_per_sec": round(float(frames_last_1s), 3),
        "frames_last_10s": frames_last_10s,
        "effective_log_hz": round(effective_hz, 3),
        "peak_log_hz": round(peak_log_hz, 3),
        "last_chunk_bytes": int(chunk_bytes),
    }
    _update_device_state(runtime.profile.id, **metrics)
    return metrics


def _record_logged_sample_metrics(runtime: DeviceRuntime, observed_ts: float) -> dict[str, Any]:
    with runtime.log_metrics_lock:
        runtime.log_timestamps.append(observed_ts)
        cutoff_1s = observed_ts - 1.0
        cutoff_10s = observed_ts - 10.0
        logged_samples_last_1s = sum(1 for ts in runtime.log_timestamps if ts >= cutoff_1s)
        while runtime.log_timestamps and runtime.log_timestamps[0] < cutoff_10s:
            runtime.log_timestamps.popleft()
        logged_samples_last_10s = len(runtime.log_timestamps)
        effective_logged_hz = logged_samples_last_10s / 10.0
    state = _get_device_state(runtime.profile.id)
    logged_samples_total = int(state.get("logged_samples_total") or 0) + 1
    peak_logged_hz = max(float(state.get("peak_logged_hz") or 0.0), effective_logged_hz)
    metrics = {
        "logged_samples_total": logged_samples_total,
        "logged_samples_last_1s": logged_samples_last_1s,
        "logged_samples_last_10s": logged_samples_last_10s,
        "effective_logged_hz": round(effective_logged_hz, 3),
        "peak_logged_hz": round(peak_logged_hz, 3),
    }
    _update_device_state(runtime.profile.id, **metrics)
    return metrics


def _begin_measurement_session(runtime: DeviceRuntime) -> None:
    runtime.next_log_due_ts = None
    runtime.startup_discard_remaining = THERMOMETER_STARTUP_DISCARD_SAMPLES
    _update_device_state(
        runtime.profile.id,
        startup_discard_remaining=runtime.startup_discard_remaining,
    )


def _discard_startup_sample(runtime: DeviceRuntime) -> bool:
    if runtime.startup_discard_remaining <= 0:
        return False
    runtime.startup_discard_remaining -= 1
    _update_device_state(
        runtime.profile.id,
        startup_discard_remaining=runtime.startup_discard_remaining,
    )
    return True


def _open_serial(profile: DeviceProfile, runtime: DeviceRuntime, timeout_ms: int | None = None) -> serial.Serial:
    timeout_s = max(0.05, float(timeout_ms if timeout_ms is not None else THERMOMETER_TIMEOUT_MS) / 1000.0)
    port = _resolve_port(profile)
    _update_device_state(profile.id, port=port, port_present=os.path.exists(port))
    ser = serial.Serial(
        port,
        baudrate=profile.baud,
        timeout=timeout_s,
        write_timeout=timeout_s,
        bytesize=serial.EIGHTBITS,
        parity=serial.PARITY_NONE,
        stopbits=serial.STOPBITS_ONE,
        xonxoff=False,
        rtscts=False,
        dsrdtr=False,
        exclusive=True,
    )
    os.set_blocking(ser.fileno(), False)
    state = _get_device_state(profile.id)
    _update_device_state(profile.id, serial_reopen_count=int(state.get("serial_reopen_count") or 0) + 1)
    return ser


def _is_optris_profile(profile: DeviceProfile) -> bool:
    return profile.id.lower().startswith("optris") or profile.device_name.upper().startswith("OPTRIS")


def _keep_serial_open_on_stale(profile: DeviceProfile) -> bool:
    return profile.mode in {"stream", "burst"}


def _stale_stream_reopen_due(profile: DeviceProfile, runtime: DeviceRuntime) -> bool:
    if profile.burst_mode:
        return False
    if not _keep_serial_open_on_stale(profile):
        return True
    now = time.monotonic()
    if runtime.stale_started_monotonic is None:
        runtime.stale_started_monotonic = now
        return False
    if now - runtime.stale_started_monotonic < THERMOMETER_OPTRIS_STALE_REOPEN_SEC:
        return False
    if (
        runtime.last_stale_reopen_monotonic is not None
        and now - runtime.last_stale_reopen_monotonic < THERMOMETER_OPTRIS_STALE_REOPEN_SEC
    ):
        return False
    runtime.last_stale_reopen_monotonic = now
    return True


def _state_from_parsed(profile: DeviceProfile, parsed: dict[str, Any], raw_value: str, observed_at: str) -> dict[str, Any]:
    return {
        "status": "ok",
        "connected": True,
        "protocol_mode": THERMOMETER_PROTOCOL,
        "last_measurement_c": parsed.get("value_c"),
        "last_measurement_raw": raw_value,
        "last_measurement_at": observed_at,
        "last_status_text": "binary stream",
        "last_binary_frame_hex": parsed.get("frame_hex"),
        "last_measurement_payload": {
            "ok": True,
            "raw_hex": raw_value,
            "parsed": parsed,
            "device_id": profile.id,
            "device_name": profile.device_name,
            "display_name": profile.display_name,
        },
        "sensor_head_temperature_c": parsed.get("sensor_head_temperature_c"),
        "controller_box_temperature_c": parsed.get("controller_box_temperature_c"),
        "object_temperature_c": parsed.get("object_temperature_c"),
        "last_error": None,
    }


def _write_measurement(
    profile: DeviceProfile,
    write_api: Any,
    value_c: float,
    raw_value: str,
    observed_at: datetime,
    status_text: str | None = None,
    extra_fields: dict[str, Any] | None = None,
) -> None:
    if write_api is None:
        return
    point = (
        Point(THERMOMETER_MEASUREMENT)
        .tag("source", profile.source_tag)
        .tag("device", profile.device_name)
        .tag("serial", profile.serial)
        .field("temperature_c", float(value_c))
        .field("raw", str(raw_value))
        .time(observed_at, WritePrecision.NS)
    )
    if status_text:
        point = point.tag("status_text", str(status_text))
    for key, value in (extra_fields or {}).items():
        if isinstance(value, (int, float)):
            point = point.field(str(key), value)
    write_api.write(bucket=INFLUX_BUCKET, org=INFLUX_ORG, record=point, write_precision=WritePrecision.NS)


def _read_binary_measurement(profile: DeviceProfile, runtime: DeviceRuntime, timeout_ms: int | None = None) -> dict[str, Any]:
    effective_timeout = timeout_ms if timeout_ms is not None else THERMOMETER_TIMEOUT_MS
    with runtime.serial_lock:
        try:
            with _open_serial(profile, runtime, effective_timeout) as ser:
                blob = ser.read(128)
        except Exception as exc:
            return {"ok": False, "error": str(exc), "device_id": profile.id}
    parsed = parse_binary_frame(blob)
    if parsed is None:
        return {"ok": False, "error": "binary frame not found", "raw_hex": bytes(blob).hex(), "device_id": profile.id}
    return {"ok": True, "raw_hex": bytes(blob).hex(), "parsed": parsed, "device_id": profile.id}


def _read_serial_chunk(ser: serial.Serial) -> bytes:
    chunk_size = max(8, THERMOMETER_STREAM_CHUNK_SIZE)
    try:
        return os.read(ser.fileno(), chunk_size)
    except BlockingIOError:
        return b""


def _read_serial_exact(ser: serial.Serial, size: int, timeout_sec: float) -> bytes:
    response = bytearray()
    deadline = time.monotonic() + timeout_sec
    while len(response) < size and time.monotonic() < deadline:
        try:
            response.extend(os.read(ser.fileno(), size - len(response)))
        except BlockingIOError:
            time.sleep(0.005)
    return bytes(response)


def _drain_serial_input(ser: serial.Serial) -> None:
    while True:
        ready, _, _ = select.select([ser.fileno()], [], [], 0)
        if not ready:
            return
        try:
            if not os.read(ser.fileno(), max(8, THERMOMETER_STREAM_CHUNK_SIZE)):
                return
        except BlockingIOError:
            return


def _read_sensor_serial(profile: DeviceProfile, ser: serial.Serial) -> str | None:
    _drain_serial_input(ser)
    ser.write(build_read_serial_number_command())
    deadline = time.monotonic() + 0.5
    response = bytearray()
    while len(response) < 3 and time.monotonic() < deadline:
        ready, _, _ = select.select([ser.fileno()], [], [], max(0.0, deadline - time.monotonic()))
        if not ready:
            break
        try:
            response.extend(os.read(ser.fileno(), 3 - len(response)))
        except BlockingIOError:
            continue
    return parse_serial_number_response(bytes(response))


def _verify_sensor_identity(profile: DeviceProfile, ser: serial.Serial) -> None:
    if not profile.sensor_serial:
        return
    try:
        detected = _read_sensor_serial(profile, ser)
    except Exception as exc:
        _update_device_state(profile.id, sensor_serial_verified=False, last_error=f"sensor identity read failed: {exc}")
        return
    verified = detected == profile.sensor_serial if detected else False
    _update_device_state(
        profile.id,
        detected_sensor_serial=detected,
        sensor_serial_verified=verified,
        last_error=None if verified else f"sensor identity mismatch: expected {profile.sensor_serial}, got {detected or 'no response'}",
    )


def _write_serial_commands(ser: serial.Serial, commands: list[bytes]) -> None:
    for command in commands:
        if not command:
            continue
        ser.write(command)


def _normalize_ir_setting_value(value: Any) -> float:
    parsed = float(value)
    if parsed < 0.05 or parsed > 1.10:
        raise ValueError("value must be between 0.050 and 1.100")
    return round(parsed, 3)


def _read_optris_factor(ser: serial.Serial, command: bytes) -> float | None:
    ser.reset_input_buffer()
    ser.write(command)
    return _read_optris_factor_response(ser)


def _read_optris_factor_response(ser: serial.Serial) -> float | None:
    deadline = time.monotonic() + 0.5
    buffer = bytearray()
    while time.monotonic() < deadline:
        chunk = bytes(ser.read(1))
        if not chunk:
            continue
        buffer.extend(chunk)
        if len(buffer) > 32:
            buffer[:] = buffer[-32:]
        for idx in range(0, max(0, len(buffer) - 1)):
            word = int.from_bytes(buffer[idx:idx + 2], "big", signed=False)
            factor = word / 1000.0
            if 0.7 <= factor <= 1.10:
                return factor
    return None


def _apply_ir_settings_on_serial(profile: DeviceProfile, ser: serial.Serial, values: dict[str, Any]) -> dict[str, Any]:
    if not _is_optris_profile(profile):
        raise ValueError("IR settings are currently supported for Optris profiles only")

    applied: dict[str, float] = {}
    source_value = values.get("ambient_source")
    if source_value is not None:
        ser.reset_input_buffer()
        command = build_optris_set_ambient_source_command(str(source_value))
        ser.write(command)
    if "ambient_fixed_c" in values:
        ser.reset_input_buffer()
        command = build_optris_set_ambient_fixed_temperature_command(values["ambient_fixed_c"])
        ser.write(command)
        ser.reset_input_buffer()
        command = build_optris_set_ambient_source_command("fixed")
        ser.write(command)
    if "emissivity" in values:
        ser.reset_input_buffer()
        command = build_optris_set_emissivity_command(values["emissivity"])
        ser.write(command)
        applied["emissivity"] = values["emissivity"]
    if "transmissivity" in values:
        ser.reset_input_buffer()
        command = build_optris_set_transmissivity_command(values["transmissivity"])
        ser.write(command)
        applied["transmissivity"] = values["transmissivity"]

    if "emissivity" not in applied:
        current = _read_optris_factor(ser, build_optris_read_emissivity_command())
        if current is not None:
            applied["emissivity"] = current
    if "transmissivity" not in applied:
        current = _read_optris_factor(ser, build_optris_read_transmissivity_command())
        if current is not None:
            applied["transmissivity"] = current

    state_updates: dict[str, Any] = {"last_ir_settings_at": datetime.now(timezone.utc).isoformat()}
    state_updates.update(applied)
    if source_value is not None:
        state_updates["ambient_compensation_source"] = str(source_value)
    if "ambient_fixed_c" in values:
        state_updates["ambient_compensation_source"] = "fixed"
        state_updates["ambient_compensation_fixed_c"] = values["ambient_fixed_c"]
    _update_device_state(profile.id, **state_updates)
    return {"success": True, "device_id": profile.id, **applied}


def _queue_ir_settings(runtime: DeviceRuntime, values: dict[str, Any]) -> dict[str, Any]:
    request_item = IrSettingRequest(values=values)
    with runtime.command_lock:
        runtime.ir_setting_requests.append(request_item)
    if not request_item.done.wait(timeout=8.0):
        return {"success": False, "error": "timed out waiting for pyrometer serial stream"}
    return request_item.result or {"success": False, "error": "empty command result"}


def _pop_ir_setting_request(runtime: DeviceRuntime) -> IrSettingRequest | None:
    with runtime.command_lock:
        if not runtime.ir_setting_requests:
            return None
        return runtime.ir_setting_requests.popleft()


def _process_ir_setting_requests(profile: DeviceProfile, runtime: DeviceRuntime, ser: serial.Serial) -> bool:
    processed = False
    stopped_stream = False
    while True:
        request_item = _pop_ir_setting_request(runtime)
        if request_item is None:
            break
        processed = True
        try:
            if profile.burst_mode and not stopped_stream:
                _write_serial_commands(ser, _stream_stop_commands(profile))
                time.sleep(0.05)
                stopped_stream = True
            request_item.result = _apply_ir_settings_on_serial(profile, ser, request_item.values)
        except Exception as exc:
            request_item.result = {"success": False, "device_id": profile.id, "error": str(exc)}
            _update_device_state(profile.id, last_error=f"IR settings failed: {exc}")
        finally:
            request_item.done.set()
    if stopped_stream:
        _prepare_stream(profile, ser)
    return processed


def _stream_start_commands(profile: DeviceProfile) -> list[bytes]:
    custom_commands = normalize_hex_commands(profile.stream_start_hex)
    if custom_commands:
        return custom_commands
    if profile.burst_mode:
        if profile.burst_command_set == "classic_ct":
            return build_classic_ct_burst_start_commands(profile.burst_channels, profile.checksum_enabled)
        return build_optris_burst_start_commands(profile.burst_channels, profile.burst_interval_ms)
    return []


def _stream_stop_commands(profile: DeviceProfile) -> list[bytes]:
    custom_commands = normalize_hex_commands(profile.stream_stop_hex)
    if custom_commands:
        return custom_commands
    if profile.burst_mode:
        if profile.burst_command_set == "classic_ct":
            return build_classic_ct_burst_stop_commands(profile.checksum_enabled)
        return build_optris_burst_stop_commands()
    return []


def _prepare_stream(profile: DeviceProfile, ser: serial.Serial) -> None:
    commands = _stream_start_commands(profile)
    if not commands:
        return
    ser.reset_input_buffer()
    _write_serial_commands(ser, commands)
    if profile.stream_start_delay_sec > 0:
        time.sleep(profile.stream_start_delay_sec)


def _stop_configured_stream(profile: DeviceProfile, ser: serial.Serial | None) -> None:
    if ser is None:
        return
    try:
        _write_serial_commands(ser, _stream_stop_commands(profile))
    except Exception:
        pass


def _parse_stream_buffer(profile: DeviceProfile, frame_buffer: bytearray) -> list[dict[str, Any]]:
    if profile.stream_frame_format == STREAM_FRAME_FORMAT_MARKED_BURST_WORDS:
        parsed, remainder = parse_marked_burst_word_frames(bytes(frame_buffer), profile.burst_channels)
        frame_buffer[:] = remainder
        return parsed
    if profile.stream_frame_format == STREAM_FRAME_FORMAT_BURST_WORDS:
        parsed, remainder = parse_burst_word_frames(bytes(frame_buffer), profile.burst_channels)
        frame_buffer[:] = remainder
        return parsed
    parsed = parse_binary_frames(bytes(frame_buffer))
    frame_buffer[:] = frame_buffer[-7:]
    return parsed


def _reset_stream_runtime(runtime: DeviceRuntime) -> None:
    runtime.last_batch_monotonic = None
    runtime.last_frame_monotonic = None
    runtime.stream_opened_monotonic = None
    runtime.stale_started_monotonic = None
    runtime.next_log_due_ts = None
    with runtime.frame_metrics_lock:
        runtime.frame_timestamps.clear()
    with runtime.log_metrics_lock:
        runtime.log_timestamps.clear()


def _close_serial(ser: serial.Serial | None) -> None:
    if ser is None:
        return
    try:
        ser.close()
    except Exception:
        pass


def _stream_is_stale(runtime: DeviceRuntime) -> bool:
    return stream_is_stale(
        now_monotonic=time.monotonic(),
        stale_after_sec=THERMOMETER_STREAM_STALE_SEC,
        last_valid_frame_monotonic=runtime.last_frame_monotonic,
        stream_opened_monotonic=runtime.stream_opened_monotonic,
    )


def _mark_stream_stale(profile: DeviceProfile) -> None:
    state = _get_device_state(profile.id)
    error_text = "stream stale; waiting for serial data" if _keep_serial_open_on_stale(profile) else "stream stale; reopening serial"
    _update_device_state(
        profile.id,
        status="degraded",
        connected=False,
        frames_last_1s=0,
        frames_per_sec=0.0,
        frames_last_10s=0,
        effective_log_hz=0.0,
        logged_samples_last_1s=0,
        logged_samples_last_10s=0,
        effective_logged_hz=0.0,
        last_error=error_text,
        stream_stale_count=int(state.get("stream_stale_count") or 0) + 1,
        last_stream_stale_at=datetime.now(timezone.utc).isoformat(),
    )


def _handle_stale_stream(profile: DeviceProfile, runtime: DeviceRuntime, frame_buffer: bytearray) -> None:
    frame_buffer.clear()
    if _keep_serial_open_on_stale(profile):
        runtime.last_batch_monotonic = None
        runtime.last_frame_monotonic = None
        runtime.next_log_due_ts = None
        with runtime.frame_metrics_lock:
            runtime.frame_timestamps.clear()
        with runtime.log_metrics_lock:
            runtime.log_timestamps.clear()
    else:
        _reset_stream_runtime(runtime)
    _mark_stream_stale(profile)
    if _keep_serial_open_on_stale(profile):
        runtime.stream_opened_monotonic = time.monotonic()


def _frame_times_for_batch(runtime: DeviceRuntime, frame_count: int) -> list[float]:
    if frame_count <= 0:
        return []
    batch_end_mono = time.monotonic()
    batch_end_wall = time.time()
    if runtime.last_batch_monotonic is None:
        batch_span = max(float(THERMOMETER_STREAM_TIMEOUT_MS) / 1000.0, 0.001)
    else:
        batch_span = max(batch_end_mono - runtime.last_batch_monotonic, 0.001)
    runtime.last_batch_monotonic = batch_end_mono
    frame_step = batch_span / float(frame_count)
    batch_start_wall = batch_end_wall - batch_span
    return [batch_start_wall + (frame_step * (idx + 1)) for idx in range(frame_count)]


def _device_loop(runtime: DeviceRuntime) -> None:
    profile = runtime.profile
    influx_client = None
    write_api = None
    ser: serial.Serial | None = None
    frame_buffer = bytearray()
    try:
        if _influx_enabled():
            influx_client = InfluxDBClient(url=INFLUX_URL, token=INFLUX_TOKEN, org=INFLUX_ORG)
            write_api = influx_client.write_api(
                write_options=WriteOptions(
                    batch_size=max(1, THERMOMETER_INFLUX_BATCH_SIZE),
                    flush_interval=max(100, THERMOMETER_INFLUX_FLUSH_MS),
                    jitter_interval=0,
                    retry_interval=5_000,
                    max_retries=3,
                    max_retry_delay=30_000,
                    exponential_base=2,
                )
            )
        while not POLL_STOP.is_set():
            port = _resolve_port(profile)
            port_present = os.path.exists(port)
            _update_device_state(profile.id, port=port, port_present=port_present)
            if not port_present:
                if ser is not None:
                    _close_serial(ser)
                    ser = None
                    frame_buffer.clear()
                    _reset_stream_runtime(runtime)
                _update_device_state(profile.id, status="missing", connected=False, last_error="device path not present")
                time.sleep(THERMOMETER_POLL_SEC)
                continue
            try:
                if ser is None:
                    ser = _open_serial(profile, runtime, THERMOMETER_STREAM_TIMEOUT_MS)
                    frame_buffer.clear()
                    _reset_stream_runtime(runtime)
                    if profile.burst_mode:
                        _stop_configured_stream(profile, ser)
                        time.sleep(0.05)
                    _verify_sensor_identity(profile, ser)
                    _prepare_stream(profile, ser)
                    _begin_measurement_session(runtime)
                    runtime.stream_opened_monotonic = time.monotonic()
                if _process_ir_setting_requests(profile, runtime, ser):
                    frame_buffer.clear()
                    _reset_stream_runtime(runtime)
                    _begin_measurement_session(runtime)
                    runtime.stream_opened_monotonic = time.monotonic()
                chunk = _read_serial_chunk(ser)
            except Exception as exc:
                if ser is not None:
                    _close_serial(ser)
                    ser = None
                frame_buffer.clear()
                _reset_stream_runtime(runtime)
                _update_device_state(profile.id, status="degraded", connected=False, last_error=str(exc))
                time.sleep(THERMOMETER_POLL_SEC)
                continue

            if not chunk:
                if _stream_is_stale(runtime):
                    _handle_stale_stream(profile, runtime, frame_buffer)
                    if _stale_stream_reopen_due(profile, runtime):
                        _close_serial(ser)
                        ser = None
                    time.sleep(THERMOMETER_POLL_SEC)
                    continue
                time.sleep(max(0.001, THERMOMETER_EMPTY_STREAM_SLEEP_SEC))
                continue

            frame_buffer.extend(chunk)
            if len(frame_buffer) > 4096:
                frame_buffer[:] = frame_buffer[-512:]

            parsed_frames = _parse_stream_buffer(profile, frame_buffer)
            if not parsed_frames:
                if _stream_is_stale(runtime):
                    _handle_stale_stream(profile, runtime, frame_buffer)
                    if _stale_stream_reopen_due(profile, runtime):
                        _close_serial(ser)
                        ser = None
                    time.sleep(THERMOMETER_POLL_SEC)
                continue

            frame_times = _frame_times_for_batch(runtime, len(parsed_frames))
            valid_frames = [
                (idx, parsed)
                for idx, parsed in enumerate(parsed_frames)
                if parsed.get("value_c") is not None and str(parsed.get("frame_hex") or "")
            ]
            if valid_frames:
                latest_idx, parsed = valid_frames[-1]
                value_c = float(parsed["value_c"])
                frame_hex = str(parsed["frame_hex"])
                observed_at = datetime.fromtimestamp(frame_times[latest_idx], tz=timezone.utc)
                observed_ts = observed_at.timestamp()
                runtime.last_frame_monotonic = time.monotonic()
                runtime.stale_started_monotonic = None
                runtime.last_stale_reopen_monotonic = None
                metric_state = _record_frame_batch_metrics(
                    runtime,
                    [frame_times[idx] for idx, _ in valid_frames],
                    len(chunk),
                )
                _update_device_state(
                    profile.id,
                    **_state_from_parsed(profile, parsed, frame_hex, observed_at.isoformat()),
                    **metric_state,
                )
                state_snapshot = _get_device_state(profile.id)
                target_hz = float(state_snapshot.get("logging_target_hz") or 0.0)
                should_log = target_hz <= 0.0
                if not should_log:
                    if runtime.next_log_due_ts is None:
                        runtime.next_log_due_ts = observed_ts
                    should_log = observed_ts >= runtime.next_log_due_ts
                if should_log:
                    if target_hz > 0.0:
                        min_interval = 1.0 / target_hz
                        next_due = runtime.next_log_due_ts if runtime.next_log_due_ts is not None else observed_ts
                        while next_due <= observed_ts:
                            next_due += min_interval
                        runtime.next_log_due_ts = next_due
                    if not _discard_startup_sample(runtime):
                        log_metric_state = _record_logged_sample_metrics(runtime, observed_ts)
                        _update_device_state(profile.id, **log_metric_state)
                        try:
                            _write_measurement(
                                profile,
                                write_api,
                                value_c,
                                frame_hex,
                                observed_at,
                                "binary stream",
                                {
                                    "channel_1_c": parsed.get("channel_1_c"),
                                    "channel_2_c": parsed.get("channel_2_c"),
                                    "channel_3_c": parsed.get("channel_3_c"),
                                    "sensor_head_temperature_c": parsed.get("sensor_head_temperature_c"),
                                    "controller_box_temperature_c": parsed.get("controller_box_temperature_c"),
                                    "object_temperature_c": parsed.get("object_temperature_c"),
                                },
                            )
                        except Exception as exc:
                            _update_device_state(profile.id, last_error=f"influx write failed: {exc}")
            elif _stream_is_stale(runtime):
                _handle_stale_stream(profile, runtime, frame_buffer)
                if _stale_stream_reopen_due(profile, runtime):
                    _close_serial(ser)
                    ser = None
                time.sleep(THERMOMETER_POLL_SEC)
    finally:
        if write_api is not None:
            try:
                write_api.flush()
            except Exception:
                pass
            try:
                write_api.close()
            except Exception:
                pass
        if ser is not None:
            _stop_configured_stream(profile, ser)
            _close_serial(ser)
        if influx_client is not None:
            influx_client.close()


def _device_loop_poll(runtime: DeviceRuntime) -> None:
    profile = runtime.profile
    influx_client = None
    write_api = None
    ser: serial.Serial | None = None
    try:
        if _influx_enabled():
            influx_client = InfluxDBClient(url=INFLUX_URL, token=INFLUX_TOKEN, org=INFLUX_ORG)
            write_api = influx_client.write_api(
                write_options=WriteOptions(
                    batch_size=max(1, THERMOMETER_INFLUX_BATCH_SIZE),
                    flush_interval=max(100, THERMOMETER_INFLUX_FLUSH_MS),
                    jitter_interval=0,
                    retry_interval=5_000,
                    max_retries=3,
                    max_retry_delay=30_000,
                    exponential_base=2,
                )
            )
        while not POLL_STOP.is_set():
            port = _resolve_port(profile)
            port_present = os.path.exists(port)
            _update_device_state(profile.id, port=port, port_present=port_present)
            if not port_present:
                ser = None
                _reset_stream_runtime(runtime)
                _update_device_state(profile.id, status="missing", connected=False, last_error="device path not present")
                time.sleep(THERMOMETER_POLL_SEC)
                continue
            try:
                with runtime.serial_lock:
                    if ser is None:
                        ser = _open_serial(profile, runtime)
                        if profile.burst_command_set == "classic_ct":
                            _write_serial_commands(ser, build_classic_ct_burst_stop_commands(profile.checksum_enabled))
                            time.sleep(0.05)
                        _verify_sensor_identity(profile, ser)
                        _begin_measurement_session(runtime)
                    with POLL_SERIAL_BUS_LOCK:
                        _process_ir_setting_requests(profile, runtime, ser)
                        _drain_serial_input(ser)
                        if _is_optris_profile(profile):
                            poll_command = b"\x53\x53"
                            response_size = (len(profile.burst_channels) * 2) + 1
                        else:
                            poll_command = b"\x01"
                            response_size = 2
                        ser.write(poll_command)
                        data = _read_serial_exact(
                            ser,
                            response_size,
                            max(0.05, THERMOMETER_TIMEOUT_MS / 1000.0),
                        )
            except Exception as exc:
                ser = None
                _reset_stream_runtime(runtime)
                _update_device_state(profile.id, status="degraded", connected=False, last_error=str(exc))
                time.sleep(THERMOMETER_POLL_SEC)
                continue

            parsed = parse_poll_response(data)
            if parsed is None:
                runtime.consecutive_poll_misses += 1
                missed = runtime.consecutive_poll_misses
                _update_device_state(
                    profile.id,
                    status="degraded" if missed >= 10 else "ok",
                    connected=missed < 10,
                    last_error=f"invalid poll response: {data.hex()!r}",
                )
                time.sleep(THERMOMETER_POLL_SEC)
                continue

            runtime.consecutive_poll_misses = 0

            value_c = parsed["value_c"]
            frame_hex = parsed["frame_hex"]
            observed_at = datetime.now(timezone.utc)
            observed_ts = observed_at.timestamp()

            runtime.last_frame_monotonic = time.monotonic()
            if runtime.stream_opened_monotonic is None:
                runtime.stream_opened_monotonic = time.monotonic()

            metric_state = dict(_record_frame_metrics(runtime, observed_ts, 2))
            metric_state.pop("status", None)
            metric_state.pop("connected", None)

            _update_device_state(
                profile.id,
                status="ok",
                connected=True,
                protocol_mode="poll",
                last_measurement_c=value_c,
                last_measurement_raw=frame_hex,
                last_measurement_at=observed_at.isoformat(),
                last_status_text="poll",
                last_binary_frame_hex=frame_hex,
                last_measurement_payload={
                    "ok": True,
                    "raw_hex": frame_hex,
                    "parsed": parsed,
                    "device_id": profile.id,
                    "device_name": profile.device_name,
                    "display_name": profile.display_name,
                },
                object_temperature_c=value_c,
                sensor_head_temperature_c=None,
                controller_box_temperature_c=None,
                last_error=None,
                **metric_state,
            )

            state_snapshot = _get_device_state(profile.id)
            target_hz = float(state_snapshot.get("logging_target_hz") or 0.0)
            poll_interval_sec = (
                1.0 / min(target_hz * 2.0, 5.0)
                if target_hz > 0.0
                else THERMOMETER_POLL_SEC
            )
            _update_device_state(profile.id, poll_interval_sec=round(poll_interval_sec, 6))
            should_log = target_hz <= 0.0
            if not should_log:
                if runtime.next_log_due_ts is None:
                    runtime.next_log_due_ts = observed_ts
                should_log = observed_ts >= runtime.next_log_due_ts
            if should_log:
                if target_hz > 0.0:
                    min_interval = 1.0 / target_hz
                    next_due = runtime.next_log_due_ts if runtime.next_log_due_ts is not None else observed_ts
                    while next_due <= observed_ts:
                        next_due += min_interval
                    runtime.next_log_due_ts = next_due
                if not _discard_startup_sample(runtime):
                    log_metric_state = _record_logged_sample_metrics(runtime, observed_ts)
                    _update_device_state(profile.id, **log_metric_state)
                    try:
                        _write_measurement(
                            profile, write_api, float(value_c), frame_hex, observed_at, "poll",
                            {"object_temperature_c": value_c},
                        )
                    except Exception as exc:
                        _update_device_state(profile.id, last_error=f"influx write failed: {exc}")

            time.sleep(max(0.001, poll_interval_sec))
    finally:
        if write_api is not None:
            try:
                write_api.flush()
            except Exception:
                pass
            try:
                write_api.close()
            except Exception:
                pass
        if influx_client is not None:
            influx_client.close()


def _selected_profile(device_id: str | None = None) -> DeviceProfile | None:
    if device_id:
        for profile in DEVICE_PROFILES:
            if profile.id == device_id:
                return profile
        return None
    selected_id = _app_health_payload().get("selected_device_id")
    if not selected_id:
        return DEVICE_PROFILES[0] if DEVICE_PROFILES else None
    return _selected_profile(str(selected_id))


@app.get("/")
def index() -> tuple[str, int, dict[str, str]]:
    return (
        Path(__file__).with_name("ui.html").read_text(encoding="utf-8"),
        200,
        {
            "Content-Type": "text/html; charset=utf-8",
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


@app.get("/ui.js")
def ui_js() -> tuple[str, int, dict[str, str]]:
    return (
        Path(__file__).with_name("ui.js").read_text(encoding="utf-8"),
        200,
        {
            "Content-Type": "application/javascript",
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


@app.get("/device-common.css")
def device_common_css() -> tuple[str, int, dict[str, str]]:
    return (
        Path(__file__).with_name("device-common.css").read_text(encoding="utf-8"),
        200,
        {"Content-Type": "text/css"},
    )


@app.get("/health")
@app.get("/healthz")
@app.get("/api/health")
def health():
    return jsonify(_app_health_payload())


@app.get("/api/device/<device_id>/health")
def device_health(device_id: str):
    if device_id not in RUNTIMES:
        return jsonify(success=False, error="unknown device"), 404
    return jsonify(_get_device_state(device_id))


@app.get("/api/measurement")
@app.get("/api/measurement/all")
def api_measurement():
    profile = _selected_profile(request.args.get("device"))
    if profile is None:
        return jsonify(success=False, error="no device profiles"), 404
    state = _get_device_state(profile.id)
    payload = state.get("last_measurement_payload")
    if isinstance(payload, dict):
        return jsonify(payload)
    return jsonify(_read_binary_measurement(profile, RUNTIMES[profile.id]))


@app.get("/api/device/<device_id>/measurement/all")
def api_device_measurement_all(device_id: str):
    profile = _selected_profile(device_id)
    if profile is None:
        return jsonify(success=False, error="unknown device"), 404
    state = _get_device_state(profile.id)
    payload = state.get("last_measurement_payload")
    if isinstance(payload, dict):
        return jsonify(payload)
    return jsonify(_read_binary_measurement(profile, RUNTIMES[profile.id]))


@app.route("/api/logging", methods=["GET", "POST"])
def api_logging():
    if request.method == "POST":
        payload = request.get_json(silent=True) or {}
        if not isinstance(payload, dict):
            return jsonify(success=False, error="Expected JSON object"), 400
        health_payload = _set_logging_target_hz(payload.get("hz"))
    else:
        health_payload = _app_health_payload()
    selected_id = health_payload.get("selected_device_id")
    state = health_payload.get("devices", {}).get(selected_id, {}) if selected_id else {}
    return jsonify(
        success=True,
        logging_target_hz=state.get("logging_target_hz"),
        logging_interval_ms=state.get("logging_interval_ms"),
        logging_presets_hz=LOGGING_PRESETS_HZ,
        effective_logged_hz=state.get("effective_logged_hz"),
        peak_logged_hz=state.get("peak_logged_hz"),
        logged_samples_last_1s=state.get("logged_samples_last_1s"),
        logged_samples_last_10s=state.get("logged_samples_last_10s"),
        logged_samples_total=state.get("logged_samples_total"),
    )


@app.route("/api/ir-settings", methods=["GET", "POST"])
@app.route("/api/device/<device_id>/ir-settings", methods=["GET", "POST"])
def api_ir_settings(device_id: str | None = None):
    profile = _selected_profile(device_id or request.args.get("device"))
    if profile is None:
        return jsonify(success=False, error="unknown device"), 404
    state = _get_device_state(profile.id)
    if request.method == "GET":
        supported = _is_optris_profile(profile)
        if supported:
            result = _queue_ir_settings(RUNTIMES[profile.id], {})
            if not result.get("success"):
                return jsonify(result), 500
            state = _get_device_state(profile.id)
        return jsonify(
            success=True,
            device_id=profile.id,
            emissivity=state.get("emissivity"),
            transmissivity=state.get("transmissivity"),
            ambient_compensation_source=state.get("ambient_compensation_source"),
            ambient_compensation_fixed_c=state.get("ambient_compensation_fixed_c"),
            last_ir_settings_at=state.get("last_ir_settings_at"),
            supported=supported,
        )

    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict):
        return jsonify(success=False, error="Expected JSON object"), 400

    values: dict[str, Any] = {}
    try:
        if payload.get("emissivity") not in (None, ""):
            values["emissivity"] = _normalize_ir_setting_value(payload.get("emissivity"))
        if payload.get("transmissivity") not in (None, ""):
            values["transmissivity"] = _normalize_ir_setting_value(payload.get("transmissivity"))
        if payload.get("ambient_source") not in (None, ""):
            ambient_source = str(payload.get("ambient_source") or "").strip().lower()
            if ambient_source not in {"fixed", "internal", "head"}:
                raise ValueError("ambient_source must be fixed or internal")
            values["ambient_source"] = "internal" if ambient_source == "head" else ambient_source
        if payload.get("ambient_fixed_c") not in (None, ""):
            ambient_fixed_c = float(payload.get("ambient_fixed_c"))
            if ambient_fixed_c < -100.0 or ambient_fixed_c > 900.0:
                raise ValueError("ambient_fixed_c must be between -100.0 and 900.0")
            values["ambient_fixed_c"] = round(ambient_fixed_c, 1)
    except (TypeError, ValueError) as exc:
        return jsonify(success=False, error=str(exc)), 400
    if not values:
        return jsonify(success=False, error="Provide emissivity, transmissivity, or ambient compensation"), 400
    if not _is_optris_profile(profile):
        return jsonify(success=False, error="IR settings are supported for Optris profiles only"), 400

    result = _queue_ir_settings(RUNTIMES[profile.id], values)
    status_code = 200 if result.get("success") else 500
    return jsonify(result), status_code


def _startup() -> None:
    for runtime in RUNTIMES.values():
        target = _device_loop_poll if runtime.profile.poll_mode else _device_loop
        thread = threading.Thread(target=target, args=(runtime,), daemon=True, name=f"pyrometer-{runtime.profile.id}")
        thread.start()


_startup()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=THERMOMETER_HTTP_PORT)
