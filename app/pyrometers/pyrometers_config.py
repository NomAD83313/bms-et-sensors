from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping


LOGGING_PRESETS_HZ = [1.0, 2.0, 5.0, 10.0, 20.0, 50.0, 100.0, 200.0]
THERMOMETER_PROTOCOL = "binary_stream"


def _env(env: Mapping[str, str], key: str, default: str) -> str:
    value = env.get(key)
    if value is None:
        return default
    value = str(value).strip()
    return value if value else default


@dataclass(frozen=True)
class PyrometerConfig:
    baud: int
    timeout_ms: int
    poll_sec: float
    stream_timeout_ms: int
    stream_stale_sec: float
    empty_stream_sleep_sec: float
    stream_chunk_size: int
    log_hz: float
    influx_batch_size: int
    influx_flush_ms: int
    measurement: str
    http_port: int
    influx_url: str
    influx_token: str
    influx_org: str
    influx_bucket: str

    @property
    def influx_enabled(self) -> bool:
        return bool(self.influx_token and self.influx_org and self.influx_bucket)


def load_config(env: Mapping[str, str] | None = None) -> PyrometerConfig:
    raw_env = env if env is not None else os.environ
    return PyrometerConfig(
        baud=int(_env(raw_env, "THERMOMETER_BAUD", "115200")),
        timeout_ms=int(_env(raw_env, "THERMOMETER_TIMEOUT_MS", "800")),
        poll_sec=float(_env(raw_env, "THERMOMETER_POLL_SEC", "1.0")),
        stream_timeout_ms=int(_env(raw_env, "THERMOMETER_STREAM_TIMEOUT_MS", "50")),
        stream_stale_sec=float(_env(raw_env, "THERMOMETER_STREAM_STALE_SEC", "3.0")),
        empty_stream_sleep_sec=float(_env(raw_env, "THERMOMETER_EMPTY_STREAM_SLEEP_SEC", "0.02")),
        stream_chunk_size=int(_env(raw_env, "THERMOMETER_STREAM_CHUNK_SIZE", "512")),
        log_hz=float(_env(raw_env, "THERMOMETER_LOG_HZ", "10")),
        influx_batch_size=int(_env(raw_env, "THERMOMETER_INFLUX_BATCH_SIZE", "500")),
        influx_flush_ms=int(_env(raw_env, "THERMOMETER_INFLUX_FLUSH_MS", "1000")),
        measurement=_env(raw_env, "THERMOMETER_MEASUREMENT", "pyrometers"),
        http_port=int(_env(raw_env, "THERMOMETER_HTTP_PORT", "3050")),
        influx_url=_env(raw_env, "INFLUX_URL", "http://influxdb:8086"),
        influx_token=str(raw_env.get("INFLUX_TOKEN", "")).strip(),
        influx_org=str(raw_env.get("INFLUX_ORG", "")).strip(),
        influx_bucket=str(raw_env.get("INFLUX_BUCKET", "")).strip(),
    )


def normalize_logging_hz(value: object, default_hz: float) -> float:
    text = str("" if value is None else value).strip().lower()
    if text in {"0", "0.0", "full", "max", "unlimited"}:
        return 0.0
    try:
        hz = float(text)
    except Exception:
        return default_hz
    if hz < 0:
        return default_hz
    return hz


def logging_interval_ms(hz: float) -> float:
    if hz <= 0:
        return 0.0
    return round(1000.0 / hz, 3)
