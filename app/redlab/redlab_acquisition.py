import json
import math
import os
import time
from pathlib import Path

from influxdb_client import Point, WritePrecision  # type: ignore
from uldaq import TcType, TempScale, ULException  # type: ignore


CHANNEL_COUNT = 8
BOOTSTRAP_WARMUP_SECONDS = 5.0
BOOTSTRAP_BATCHES = 6
BOOTSTRAP_STABLE_TAIL = 3
BOOTSTRAP_OUTLIER_DELTA_C = 5.0
OUTLIER_MIN_STEP_C = float(os.getenv("REDLAB_OUTLIER_MIN_STEP_C", "300"))
OUTLIER_MIN_RATE_C_PER_SEC = float(os.getenv("REDLAB_OUTLIER_MIN_RATE_C_PER_SEC", "500"))
ACTIVE_CHANNELS_PATH = Path("/runtime/redlab_channels.json")
_CHANNEL_NAME_CACHE: dict[str, str] = {}
_CHANNEL_NAME_CACHE_MTIME_NS: int | None = None
_CHANNEL_NAME_CACHE_DEVICE_ID: str | None = None
TC_TYPE_LIMITS_C: dict[str, tuple[float, float]] = {
    "B": (0.0, 1820.0),
    "E": (-270.0, 1000.0),
    "J": (-210.0, 1200.0),
    "K": (-270.0, 1372.0),
    "N": (-270.0, 1300.0),
    "R": (-50.0, 1768.0),
    "S": (-50.0, 1768.0),
    "T": (-270.0, 400.0),
}


def new_filter_state() -> dict:
    return {"last_valid_by_channel": {}}


def _channel_names_for_device(device_id: str) -> dict[str, str]:
    global _CHANNEL_NAME_CACHE, _CHANNEL_NAME_CACHE_MTIME_NS, _CHANNEL_NAME_CACHE_DEVICE_ID
    try:
        stat = ACTIVE_CHANNELS_PATH.stat()
        mtime_ns = int(stat.st_mtime_ns)
    except Exception:
        _CHANNEL_NAME_CACHE = {}
        _CHANNEL_NAME_CACHE_MTIME_NS = None
        return {}

    if _CHANNEL_NAME_CACHE_MTIME_NS == mtime_ns and _CHANNEL_NAME_CACHE_DEVICE_ID == device_id:
        return dict(_CHANNEL_NAME_CACHE)

    try:
        raw = json.loads(ACTIVE_CHANNELS_PATH.read_text(encoding="utf-8"))
    except Exception:
        _CHANNEL_NAME_CACHE = {}
        _CHANNEL_NAME_CACHE_MTIME_NS = mtime_ns
        _CHANNEL_NAME_CACHE_DEVICE_ID = device_id
        return {}

    if not isinstance(raw, dict):
        _CHANNEL_NAME_CACHE = {}
        _CHANNEL_NAME_CACHE_MTIME_NS = mtime_ns
        _CHANNEL_NAME_CACHE_DEVICE_ID = device_id
        return {}

    names: dict[str, str] = {}
    for ch in range(CHANNEL_COUNT):
        base_key = f"ch{ch}"
        scoped_key = f"{device_id}|{base_key}" if device_id else ""
        value = raw.get(scoped_key) if scoped_key and scoped_key in raw else raw.get(base_key)
        if not isinstance(value, dict):
            continue
        name = str(value.get("name", "")).strip()
        if name:
            names[base_key] = name

    _CHANNEL_NAME_CACHE = dict(names)
    _CHANNEL_NAME_CACHE_MTIME_NS = mtime_ns
    _CHANNEL_NAME_CACHE_DEVICE_ID = device_id
    return names


def build_points(batch: dict, device_id: str) -> tuple[list[Point], list[str]]:
    points = []
    log_data = []
    point_device_id = device_id or "redlab_unknown"
    channel_names = _channel_names_for_device(point_device_id)

    for ch, temp in sorted(batch["values"].items()):
        ch_key = f"ch{ch}"
        point = Point("redlab").tag("device", point_device_id).tag("channel", ch_key)
        channel_name = channel_names.get(ch_key)
        if channel_name:
            point = point.tag("channel_name", channel_name)
        points.append(point.field("value", float(temp)).time(batch["ts_ns"], WritePrecision.NS))
        log_data.append(f"CH{ch}:{temp:.1f}")
    return points, log_data


def median(values):
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def filter_bootstrap_batches(batches: list[dict]) -> tuple[list[dict], int]:
    tail = batches[-BOOTSTRAP_STABLE_TAIL:]
    stable_channels = set.intersection(*(set(batch["values"].keys()) for batch in tail))
    if not stable_channels:
        return [], 0

    reference = {
        ch: median([batch["values"][ch] for batch in tail])
        for ch in stable_channels
    }

    filtered = []
    dropped = 0
    for batch in batches:
        kept_values = {}
        for ch, temp in batch["values"].items():
            ref = reference.get(ch)
            if ref is None or abs(temp - ref) > BOOTSTRAP_OUTLIER_DELTA_C:
                dropped += 1
                continue
            kept_values[ch] = temp
        if kept_values:
            filtered.append({"ts_ns": batch["ts_ns"], "values": kept_values})
    return filtered, dropped


def _channel_tc_type_name(ai_config, channel: int) -> str | None:
    if ai_config is None:
        return None
    try:
        tc_type = ai_config.get_chan_tc_type(channel)
    except Exception:
        return None
    return getattr(tc_type, "name", None)


def _physical_limits_for_channel(ai_config, channel: int) -> tuple[float, float] | None:
    tc_type_name = _channel_tc_type_name(ai_config, channel)
    if not tc_type_name:
        return None
    return TC_TYPE_LIMITS_C.get(tc_type_name)


def _clear_channel_filter_state(active_channels: set[int], filter_state: dict) -> None:
    last_valid = filter_state["last_valid_by_channel"]
    stale_channels = [ch for ch in last_valid if ch not in active_channels]
    for ch in stale_channels:
        last_valid.pop(ch, None)


def sanitize_temperature_values(
    values: dict[int, float],
    ai_config,
    active_channels: set[int],
    sample_ts: float,
    filter_state: dict,
) -> tuple[dict[int, float], int]:
    filtered: dict[int, float] = {}
    dropped = 0
    last_valid = filter_state["last_valid_by_channel"]
    _clear_channel_filter_state(active_channels, filter_state)

    for ch, temp in sorted(values.items()):
        if not math.isfinite(temp):
            dropped += 1
            print(f"!!! Dropping non-finite temperature on CH{ch}: {temp!r}")
            continue

        limits = _physical_limits_for_channel(ai_config, ch)
        if limits is not None:
            min_temp, max_temp = limits
            if temp < min_temp or temp > max_temp:
                dropped += 1
                tc_type_name = _channel_tc_type_name(ai_config, ch) or "?"
                print(
                    f"!!! Dropping out-of-range temperature on CH{ch}: "
                    f"{temp:.3f} C for TcType {tc_type_name} "
                    f"(allowed {min_temp:.1f}..{max_temp:.1f} C)"
                )
                continue

        prev = last_valid.get(ch)
        if prev is not None:
            prev_temp, prev_ts = prev
            dt = max(sample_ts - prev_ts, 1e-6)
            delta = abs(temp - prev_temp)
            if delta >= OUTLIER_MIN_STEP_C and (delta / dt) >= OUTLIER_MIN_RATE_C_PER_SEC:
                dropped += 1
                print(
                    f"!!! Dropping spike on CH{ch}: {prev_temp:.3f} -> {temp:.3f} C "
                    f"(delta {delta:.3f} C over {dt:.3f} s, "
                    f"thresholds step>={OUTLIER_MIN_STEP_C:.1f} rate>={OUTLIER_MIN_RATE_C_PER_SEC:.1f}/s)"
                )
                continue

        filtered[ch] = temp
        last_valid[ch] = (temp, sample_ts)

    return filtered, dropped


def channel_log_data(values: dict[int, float]) -> list[str]:
    return [f"CH{ch}:{temp:.1f}" for ch, temp in sorted(values.items())]


def load_active_channels() -> set[int]:
    try:
        raw = json.loads(ACTIVE_CHANNELS_PATH.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return set(range(CHANNEL_COUNT))
        active = set()
        for ch in range(CHANNEL_COUNT):
            val = raw.get(f"ch{ch}", True)
            if isinstance(val, dict):
                enabled = bool(val.get("enabled", True))
            else:
                enabled = bool(val)
            if enabled:
                active.add(ch)
        return active
    except Exception:
        return set(range(CHANNEL_COUNT))


def configure_tc_channels(ai_config) -> None:
    for ch in range(CHANNEL_COUNT):
        ai_config.set_chan_tc_type(ch, TcType.K)


def _read_batch_single(ai_device, active_channels: set[int]) -> tuple[dict[int, float], int]:
    values: dict[int, float] = {}
    for ch in range(CHANNEL_COUNT):
        if ch not in active_channels:
            continue
        try:
            values[ch] = float(ai_device.t_in(ch, TempScale.CELSIUS))
        except ULException as e:
            if e.error_code == 85:
                continue
            print(f"\n!!! Hardware error on CH{ch}: {e}")
            raise RuntimeError(f"hardware error on CH{ch}: {e}") from e
    return values, 0


def _read_batch_list(ai_device, active_channels: set[int]) -> tuple[dict[int, float], int]:
    try:
        temps = ai_device.t_in_list(
            0,
            CHANNEL_COUNT - 1,
            TempScale.CELSIUS,
            ignore_open_connection=True,
        )
    except ULException as e:
        if e.error_code == 85:
            return _read_batch_single(ai_device, active_channels)
        print(f"\n!!! Hardware error on scan: {e}")
        return {}, 0
    values: dict[int, float] = {}
    skipped = 0
    for ch in range(CHANNEL_COUNT):
        if ch not in active_channels:
            continue
        try:
            val = float(temps[ch])
            if val == -9999:
                skipped += 1
                continue
            values[ch] = val
        except Exception:
            continue
    return values, skipped


def read_batch(
    ai_device,
    ai_config,
    active_channels: set[int],
    filter_state: dict,
    read_mode: str,
) -> tuple[dict[int, float], int]:
    if read_mode == "list":
        values, skipped = _read_batch_list(ai_device, active_channels)
    else:
        values, skipped = _read_batch_single(ai_device, active_channels)
    filtered_values, dropped = sanitize_temperature_values(
        values,
        ai_config,
        active_channels,
        time.monotonic(),
        filter_state,
    )
    return filtered_values, skipped + dropped
