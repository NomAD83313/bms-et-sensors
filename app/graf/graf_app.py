import os
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask
from graf_csv_helpers import (
    almemo_csv_column_name,
    matter_csv_column_name,
    messkluppe_csv_column_name,
    append_series_rows,
    mscl_csv_column_name,
    redlab_csv_column_name,
    pyrometers_csv_column_name,
)
from graf_backend_services import build_backend_services
from graf_data_flows import (
    build_all_export_response,
    build_single_export_response,
    load_dashboard_panels,
)
from graf_redlab_state import load_redlab_channels, save_redlab_channels
from graf_request_helpers import (
    resolve_dashboard_request,
    resolve_export_request,
)
from graf_routes import register_routes
from graf_series_helpers import duration_to_ns
from graf_views import VIEW_CONFIGS, render_index

app = Flask(__name__)

INFLUX_URL = "http://influxdb:8086"
INFLUX_TOKEN = os.getenv("INFLUX_TOKEN", "")
INFLUX_ORG = os.getenv("INFLUX_ORG", "")
INFLUX_BUCKET = os.getenv("INFLUX_BUCKET", "")

DEFAULT_RANGE = os.getenv("GRAF_APP_DEFAULT_RANGE", "5m")
DEFAULT_REFRESH_SEC = int(os.getenv("GRAF_APP_REFRESH_SEC", "5"))
MSCL_MEASUREMENT = "mscl_sensors"
MSCL_SOURCE = "mscl_config_stream"
MSCL_SOURCE_EXTRA = "mscl_node_export"
MSCL_CHANNEL = "ch1"
REDLAB_MEASUREMENT = "redlab"
ALMEMO_MEASUREMENT = "almemo_live"
THERMOMETER_MEASUREMENT = "pyrometers"
MESSKLUPPE_MEASUREMENT = "messkluppe_sensor"
MATTER_MEASUREMENT = "matter_sensor"
REDLAB_CHANNEL_STATE_PATH = Path("/runtime/redlab_channels.json")
REDLAB_CHANNEL_KEYS = [f"ch{i}" for i in range(8)]

ALLOWED_RANGES = {
    "30s": 30,
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "1h": 3600,
    "6h": 21600,
    "24h": 86400,
}

SAMPLE_PRESETS = {
    "auto": {"label": "Auto", "duration": None, "sec": None},
    "16hz": {"label": "16 Hz", "duration": "62ms", "sec": 1.0 / 16.0},
    "8hz": {"label": "8 Hz", "duration": "125ms", "sec": 1.0 / 8.0},
    "4hz": {"label": "4 Hz", "duration": "250ms", "sec": 1.0 / 4.0},
    "2hz": {"label": "2 Hz", "duration": "500ms", "sec": 1.0 / 2.0},
    "1hz": {"label": "1 Hz", "duration": "1s", "sec": 1.0},
    "5s": {"label": "1 / 5 sec", "duration": "5s", "sec": 5.0},
    "10s": {"label": "1 / 10 sec", "duration": "10s", "sec": 10.0},
    "30s": {"label": "1 / 30 sec", "duration": "30s", "sec": 30.0},
    "60s": {"label": "1 / 60 sec", "duration": "1m", "sec": 60.0},
    "120s": {"label": "1 / 120 sec", "duration": "2m", "sec": 120.0},
    "300s": {"label": "1 / 300 sec", "duration": "5m", "sec": 300.0},
}

MAX_POINTS_PER_SERIES = 20000
DEFAULT_TARGET_POINTS = 520
MIN_TARGET_POINTS = 160
MAX_TARGET_POINTS = 700


def _safe_range(value: str) -> str:
    text = str(value or DEFAULT_RANGE).strip().lower()
    if text == "custom":
        return "custom"
    return text if text in ALLOWED_RANGES else DEFAULT_RANGE


def _safe_target_points(value: str | None) -> int:
    try:
        n = int(str(value or "").strip())
    except Exception:
        n = DEFAULT_TARGET_POINTS
    return max(MIN_TARGET_POINTS, min(MAX_TARGET_POINTS, n))


def _parse_iso_ts(raw: str | None) -> datetime | None:
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        else:
            dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _safe_sample_key(value: str | None) -> str:
    key = str(value or "auto").strip().lower()
    return key if key in SAMPLE_PRESETS else "auto"


def _window_for_target_points(range_seconds: int, target_points: int) -> str:
    range_seconds = max(1, int(range_seconds))
    target_points = max(1, int(target_points))
    desired_step_sec = float(range_seconds) / float(target_points)

    candidates: list[tuple[str, float]] = [
        ("62ms", 0.062),
        ("125ms", 0.125),
        ("250ms", 0.250),
        ("500ms", 0.500),
        ("1s", 1.0),
        ("2s", 2.0),
        ("5s", 5.0),
        ("10s", 10.0),
        ("30s", 30.0),
        ("1m", 60.0),
        ("2m", 120.0),
        ("5m", 300.0),
        ("10m", 600.0),
        ("30m", 1800.0),
        ("1h", 3600.0),
    ]
    best_label = candidates[0][0]
    best_diff = abs(candidates[0][1] - desired_step_sec)
    for label, sec in candidates[1:]:
        diff = abs(sec - desired_step_sec)
        if diff < best_diff:
            best_diff = diff
            best_label = label
    return best_label


def _estimated_points_for_window(range_seconds: int, window: str) -> int:
    window_ns = duration_to_ns(window)
    if not window_ns or window_ns <= 0:
        return max(1, int(range_seconds))
    window_sec = float(window_ns) / 1_000_000_000.0
    return max(1, int(float(range_seconds) / window_sec))


backend_services = build_backend_services(
    influx_url=INFLUX_URL,
    influx_token=INFLUX_TOKEN,
    influx_org=INFLUX_ORG,
    influx_bucket=INFLUX_BUCKET,
    mscl_measurement=MSCL_MEASUREMENT,
    mscl_source=MSCL_SOURCE,
    mscl_source_extra=MSCL_SOURCE_EXTRA,
    mscl_channel=MSCL_CHANNEL,
    redlab_measurement=REDLAB_MEASUREMENT,
    almemo_measurement=ALMEMO_MEASUREMENT,
    pyrometers_measurement=THERMOMETER_MEASUREMENT,
    messkluppe_measurement=MESSKLUPPE_MEASUREMENT,
    matter_measurement=MATTER_MEASUREMENT,
    parse_iso_ts_fn=_parse_iso_ts,
)

route_context = {
    "render_index": render_index,
    "default_range": DEFAULT_RANGE,
    "default_refresh_sec": DEFAULT_REFRESH_SEC,
    "allowed_ranges": ALLOWED_RANGES,
    "influx_url": INFLUX_URL,
    "influx_token": INFLUX_TOKEN,
    "influx_org": INFLUX_ORG,
    "influx_bucket": INFLUX_BUCKET,
    "load_redlab_channels": load_redlab_channels,
    "save_redlab_channels": save_redlab_channels,
    "redlab_channel_state_path": REDLAB_CHANNEL_STATE_PATH,
    "redlab_channel_keys": REDLAB_CHANNEL_KEYS,
    "resolve_dashboard_request": resolve_dashboard_request,
    "resolve_export_request": resolve_export_request,
    "sample_presets": SAMPLE_PRESETS,
    "max_points_per_series": MAX_POINTS_PER_SERIES,
    "safe_range_fn": _safe_range,
    "safe_sample_key_fn": _safe_sample_key,
    "safe_target_points_fn": _safe_target_points,
    "parse_iso_ts_fn": _parse_iso_ts,
    "window_for_target_points_fn": _window_for_target_points,
    "estimated_points_for_window_fn": _estimated_points_for_window,
    "view_configs": VIEW_CONFIGS,
    "load_dashboard_panels": load_dashboard_panels,
    "build_single_export_response": build_single_export_response,
    "build_all_export_response": build_all_export_response,
    "append_series_rows_fn": append_series_rows,
    "mscl_csv_column_name_fn": mscl_csv_column_name,
    "redlab_csv_column_name_fn": redlab_csv_column_name,
    "pyrometers_csv_column_name_fn": pyrometers_csv_column_name,
    "messkluppe_csv_column_name_fn": messkluppe_csv_column_name,
    "matter_csv_column_name_fn": matter_csv_column_name,
    "almemo_csv_column_name_fn": almemo_csv_column_name,
    "mscl_source": MSCL_SOURCE,
    "mscl_source_extra": MSCL_SOURCE_EXTRA,
    **backend_services,
}

register_routes(app, route_context)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=3010)
