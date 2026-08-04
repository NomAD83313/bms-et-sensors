from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Mapping

REGISTRY_PATH = os.getenv("PYROMETERS_REGISTRY", "/runtime/pyrometers-devices.json")
STREAM_FRAME_FORMAT_MARKED_AAAA = "marked_aaaa"
STREAM_FRAME_FORMAT_MARKED_BURST_WORDS = "marked_burst_words"
STREAM_FRAME_FORMAT_BURST_WORDS = "burst_words"

_DEFAULT_BURST_CHANNELS = ("target_act", "internal", "box", "target_act")
_DEFAULT_BURST_COMMAND_SET = "optris_cti"

_TYPE_DEFAULTS = {
    "microeps": {
        "device_name": "thermoMETER_CT",
        "ui_subtitle": "Unified pyrometer monitor for compatible Infrared Online Sensor Adapter devices.",
    },
    "optris": {
        "device_name": "OPTRIS_CT",
        "ui_subtitle": "Unified pyrometer monitor for compatible Infrared Online Sensor Adapter devices.",
    },
}


@dataclass(frozen=True)
class DeviceProfile:
    id: str
    port_env_key: str
    default_port: str
    device_name: str
    display_name: str
    ui_subtitle: str
    source_tag: str
    serial: str
    sensor_serial: str
    match_tokens: tuple[str, ...]
    baud: int = 115200
    mode: str = "stream"
    poll_mode: bool = False
    stream_frame_format: str = STREAM_FRAME_FORMAT_MARKED_AAAA
    burst_interval_ms: int = 100
    burst_channels: tuple[str, ...] = _DEFAULT_BURST_CHANNELS
    burst_command_set: str = _DEFAULT_BURST_COMMAND_SET
    checksum_enabled: bool = True
    stream_start_hex: tuple[str, ...] = ()
    stream_stop_hex: tuple[str, ...] = ()
    stream_start_delay_sec: float = 0.05

    @property
    def burst_mode(self) -> bool:
        return self.mode == "burst"


def build_device_profiles(env: Mapping[str, str] | None = None) -> list[DeviceProfile]:
    registry_path = (env or os.environ).get("PYROMETERS_REGISTRY", REGISTRY_PATH)
    try:
        with open(registry_path) as f:
            entries = json.load(f)
    except FileNotFoundError:
        entries = []

    profiles: list[DeviceProfile] = []
    for entry in entries:
        device_id = entry["id"]
        device_type = entry.get("type", "microeps")
        serial = entry["serial"]
        symlink = entry["symlink"]
        defaults = _TYPE_DEFAULTS.get(device_type, _TYPE_DEFAULTS["microeps"])
        raw_checksum_enabled = entry.get("checksum_enabled", True)
        checksum_enabled = (
            raw_checksum_enabled
            if isinstance(raw_checksum_enabled, bool)
            else str(raw_checksum_enabled).strip().lower() not in {"0", "false", "no", "off"}
        )

        mode = str(entry.get("mode", "stream") or "stream").strip().lower()
        burst_channels = tuple(
            str(value).strip().lower()
            for value in entry.get("burst_channels", _DEFAULT_BURST_CHANNELS)
            if str(value).strip()
        )
        profiles.append(DeviceProfile(
            id=device_id,
            port_env_key=f"PYROMETER_PORT_{device_id.upper()}",
            default_port=f"/dev/{symlink}",
            device_name=entry.get("device_name") or defaults["device_name"],
            display_name=entry.get("display_name") or device_id,
            ui_subtitle=entry.get("ui_subtitle") or defaults["ui_subtitle"],
            source_tag=device_id,
            serial=serial,
            sensor_serial=str(entry.get("sensor_serial") or "").strip(),
            match_tokens=(serial.lower(),),
            baud=int(entry.get("baud") or 115200),
            mode=mode,
            poll_mode=mode == "poll",
            stream_frame_format=str(entry.get("stream_frame_format", STREAM_FRAME_FORMAT_MARKED_AAAA)).strip().lower(),
            burst_interval_ms=int(entry.get("burst_interval_ms") or 100),
            burst_channels=burst_channels or _DEFAULT_BURST_CHANNELS,
            burst_command_set=str(entry.get("burst_command_set", _DEFAULT_BURST_COMMAND_SET)).strip().lower(),
            checksum_enabled=checksum_enabled,
            stream_start_hex=tuple(str(value).strip() for value in entry.get("stream_start_hex", ()) if str(value).strip()),
            stream_stop_hex=tuple(str(value).strip() for value in entry.get("stream_stop_hex", ()) if str(value).strip()),
            stream_start_delay_sec=float(entry.get("stream_start_delay_sec") or 0.05),
        ))
    return profiles


def summarize_devices_status(devices: Mapping[str, Mapping[str, object]]) -> str:
    values = list(devices.values())
    if any(bool(item.get("connected")) for item in values):
        return "ok"
    if any(bool(item.get("port_present")) for item in values):
        return "degraded"
    if any(str(item.get("status") or "") not in {"missing", ""} for item in values):
        return "degraded"
    return "missing"


def preferred_device_id(devices: Mapping[str, Mapping[str, object]]) -> str | None:
    for item in devices.values():
        if bool(item.get("connected")):
            return str(item.get("id") or "")
    for item in devices.values():
        if bool(item.get("port_present")):
            return str(item.get("id") or "")
    for item in devices.values():
        value = str(item.get("id") or "")
        if value:
            return value
    return None
