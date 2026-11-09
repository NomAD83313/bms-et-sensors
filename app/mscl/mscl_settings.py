import os


def _env_bool(name, default=False):
    raw = os.getenv(name)
    if raw is None:
        return bool(default)
    return str(raw).strip().lower() not in ("0", "false", "no", "off")


def _env_int(name, default):
    raw = os.getenv(name)
    if raw is None:
        return int(default)
    try:
        return int(raw)
    except (TypeError, ValueError):
        return int(default)


def _env_float(name, default):
    raw = os.getenv(name)
    if raw is None:
        return float(default)
    try:
        return float(raw)
    except (TypeError, ValueError):
        return float(default)


def _env_int_list(name, default=None):
    raw = os.getenv(name)
    if raw is None:
        return list(default or [])
    out = []
    for part in str(raw).split(","):
        item = str(part).strip()
        if not item:
            continue
        try:
            out.append(int(item))
        except (TypeError, ValueError):
            continue
    return out


INFLUX_URL = "http://influxdb:8086"
INFLUX_TOKEN = os.getenv("INFLUX_TOKEN")
INFLUX_ORG = os.getenv("INFLUX_ORG")
INFLUX_BUCKET = os.getenv("INFLUX_BUCKET")

MSCL_MEASUREMENT = "mscl_sensors"
MSCL_NODE_IDS = _env_int_list("MSCL_NODE_IDS", [16904])
MSCL_ONLY_CHANNEL_1 = _env_bool("MSCL_ONLY_CHANNEL_1", False)
MSCL_STREAM_ENABLED = _env_bool("MSCL_STREAM_ENABLED", True)
MSCL_STREAM_READ_TIMEOUT_MS = _env_int("MSCL_STREAM_READ_TIMEOUT_MS", 20)
MSCL_STREAM_IDLE_SLEEP = _env_float("MSCL_STREAM_IDLE_SLEEP", 0.005)
MSCL_STREAM_BATCH_SIZE = _env_int("MSCL_STREAM_BATCH_SIZE", 5000)
MSCL_STREAM_FLUSH_INTERVAL_MS = _env_int("MSCL_STREAM_FLUSH_INTERVAL_MS", 500)
MSCL_STREAM_QUEUE_MAX = _env_int("MSCL_STREAM_QUEUE_MAX", 5000)
MSCL_STREAM_QUEUE_WAIT_MS = _env_int("MSCL_STREAM_QUEUE_WAIT_MS", 200)
MSCL_STREAM_DROP_WARN_SEC = _env_float("MSCL_STREAM_DROP_WARN_SEC", 30.0)
MSCL_STREAM_DROP_LOG_THROTTLE_SEC = _env_float("MSCL_STREAM_DROP_LOG_THROTTLE_SEC", 30.0)
MSCL_STREAM_LOG_INTERVAL_SEC = _env_float("MSCL_STREAM_LOG_INTERVAL_SEC", 5.0)

MSCL_EXPORT_INFLUX_BATCH = _env_int("MSCL_EXPORT_INFLUX_BATCH", 5000)

MSCL_SOURCE_RADIO_TAG = "mscl_config_stream"
MSCL_SOURCE_NODE_EXPORT_TAG = "mscl_node_export"

# Backward-compat aliases used across the app.
MSCL_SOURCE_RADIO = MSCL_SOURCE_RADIO_TAG
MSCL_SOURCE_NODE_EXPORT = MSCL_SOURCE_NODE_EXPORT_TAG

__all__ = [
    "INFLUX_URL",
    "INFLUX_TOKEN",
    "INFLUX_ORG",
    "INFLUX_BUCKET",
    "MSCL_MEASUREMENT",
    "MSCL_NODE_IDS",
    "MSCL_ONLY_CHANNEL_1",
    "MSCL_STREAM_ENABLED",
    "MSCL_STREAM_READ_TIMEOUT_MS",
    "MSCL_STREAM_IDLE_SLEEP",
    "MSCL_STREAM_BATCH_SIZE",
    "MSCL_STREAM_FLUSH_INTERVAL_MS",
    "MSCL_STREAM_QUEUE_MAX",
    "MSCL_STREAM_QUEUE_WAIT_MS",
    "MSCL_STREAM_DROP_WARN_SEC",
    "MSCL_STREAM_DROP_LOG_THROTTLE_SEC",
    "MSCL_STREAM_LOG_INTERVAL_SEC",
    "MSCL_EXPORT_INFLUX_BATCH",
    "MSCL_SOURCE_RADIO_TAG",
    "MSCL_SOURCE_NODE_EXPORT_TAG",
    "MSCL_SOURCE_RADIO",
    "MSCL_SOURCE_NODE_EXPORT",
]
