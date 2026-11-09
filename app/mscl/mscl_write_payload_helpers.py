from typing import Any


INT_FIELDS = (
    "sample_rate",
    "tx_power",
    "input_range",
    "unit",
    "cjc_unit",
    "low_pass_filter",
    "storage_limit_mode",
    "lost_beacon_timeout",
    "diagnostic_interval",
    "default_mode",
    "inactivity_timeout",
    "check_radio_interval",
    "data_mode",
    "transducer_type",
    "sensor_type",
    "wire_type",
)

INT_CACHE_KEYS = {
    "sample_rate": "current_rate",
    "tx_power": "current_power",
    "input_range": "current_input_range",
    "unit": "current_unit",
    "cjc_unit": "current_cjc_unit",
    "low_pass_filter": "current_low_pass",
    "storage_limit_mode": "current_storage_limit_mode",
    "lost_beacon_timeout": "current_lost_beacon_timeout",
    "diagnostic_interval": "current_diagnostic_interval",
    "default_mode": "current_default_mode",
    "inactivity_timeout": "current_inactivity_timeout",
    "check_radio_interval": "current_check_radio_interval",
    "data_mode": "current_data_mode",
    "transducer_type": "current_transducer_type",
    "sensor_type": "current_sensor_type",
    "wire_type": "current_wire_type",
}

BOOL_FIELDS = (
    "lost_beacon_enabled",
    "diagnostic_enabled",
    "inactivity_enabled",
)

DERIVED_BOOL_FROM_TIMEOUT = {
    "lost_beacon_enabled": "lost_beacon_timeout",
    "diagnostic_enabled": "diagnostic_interval",
    "inactivity_enabled": "inactivity_timeout",
}


def to_opt_int(value: Any):
    if value is None:
        return None
    if isinstance(value, str):
        vv = value.strip()
        if vv == "":
            return None
        value = vv
    try:
        return int(value)
    except Exception:
        return None


def to_opt_bool(value: Any):
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        s = value.strip().lower()
        if s in ("1", "true", "yes", "on"):
            return True
        if s in ("0", "false", "no", "off", ""):
            return False
    if isinstance(value, (int, float)):
        return bool(value)
    return False


def _has(data: dict[str, Any], key: str) -> bool:
    return key in data


def _normalize_channels(data: dict[str, Any], present: set[str]) -> list[int]:
    channels = data.get("channels")
    if "channels" not in present or not isinstance(channels, list):
        return [1]
    normalized = [int(ch) for ch in channels if to_opt_int(ch) in (1, 2)]
    return normalized if normalized else [1]


def normalize_write_payload(data: dict[str, Any], cached: dict[str, Any]) -> dict[str, Any]:
    present = {k for k in INT_FIELDS + BOOL_FIELDS + ("channels",) if _has(data, k)}

    ints: dict[str, Any] = {}
    for field in INT_FIELDS:
        ints[field] = to_opt_int(data.get(field)) if field in present else None

    bools: dict[str, bool] = {}
    for field in BOOL_FIELDS:
        bools[field] = to_opt_bool(data.get(field)) if field in present else False

    # Preserve existing behavior: sample/tx cache backfill only if either is missing.
    if ints["sample_rate"] is None or ints["tx_power"] is None:
        for field in ("sample_rate", "tx_power", "input_range", "unit", "cjc_unit", "low_pass_filter"):
            if field in present and ints[field] is None:
                ints[field] = cached.get(INT_CACHE_KEYS[field])
        if ints["sample_rate"] is None:
            ints["sample_rate"] = cached.get(INT_CACHE_KEYS["sample_rate"])
        if ints["tx_power"] is None:
            ints["tx_power"] = cached.get(INT_CACHE_KEYS["tx_power"])

    for field in INT_FIELDS:
        if field in ("sample_rate", "tx_power", "input_range", "unit", "cjc_unit", "low_pass_filter"):
            continue
        if field in present and ints[field] is None:
            ints[field] = cached.get(INT_CACHE_KEYS[field])

    for field, timeout_field in DERIVED_BOOL_FROM_TIMEOUT.items():
        if field not in present:
            timeout_value = ints.get(timeout_field)
            bools[field] = bool(timeout_value is not None and int(timeout_value) > 0)

    channels = _normalize_channels(data, present)

    return {
        "sample_rate": ints["sample_rate"],
        "tx_power": ints["tx_power"],
        "channels": channels,
        "input_range": ints["input_range"],
        "unit": ints["unit"],
        "cjc_unit": ints["cjc_unit"],
        "low_pass_filter": ints["low_pass_filter"],
        "storage_limit_mode": ints["storage_limit_mode"],
        "lost_beacon_timeout": ints["lost_beacon_timeout"],
        "diagnostic_interval": ints["diagnostic_interval"],
        "lost_beacon_enabled": bools["lost_beacon_enabled"],
        "diagnostic_enabled": bools["diagnostic_enabled"],
        "default_mode": ints["default_mode"],
        "inactivity_timeout": ints["inactivity_timeout"],
        "inactivity_enabled": bools["inactivity_enabled"],
        "check_radio_interval": ints["check_radio_interval"],
        "data_mode": ints["data_mode"],
        "transducer_type": ints["transducer_type"],
        "sensor_type": ints["sensor_type"],
        "wire_type": ints["wire_type"],
    }
