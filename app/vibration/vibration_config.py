"""Bus and sensor registry for the vibration collector.

The registry is runtime state (edited in the UI) stored as JSON under the
mounted data directory. Missing or invalid files fall back to defaults so the
service always starts; the port is never guessed because other USB serial
adapters on the host (ALMEMO, pyrometers) must not receive Modbus traffic.
"""

from __future__ import annotations

import copy
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

try:
    from . import wtvb01_protocol as proto
except ImportError:  # Flat import inside the container.
    import wtvb01_protocol as proto

MIN_POLL_SEC = 0.0
MAX_POLL_SEC = 3600.0
MAX_SENSORS = 32
# Periodic FIFO capture (FFT) per sensor, switched by fft_periodic. Each capture pauses polling
# for about 4.5 s, so the interval has a lower bound.
MIN_FFT_INTERVAL_SEC = 5.0
MAX_FFT_INTERVAL_SEC = 86400.0
# Lower frequency bound for velocity RMS computed from the spectrum.
DEFAULT_VELOCITY_MIN_HZ = 2.0
VELOCITY_RMS_MIN_HZ_CHOICES = (2.0, 10.0)
# A port of this form is the built-in simulated sensor bus (vibration_sim).
SIM_PORT_PREFIX = "sim://"
MIN_FFT_AVERAGES = 1
MAX_FFT_AVERAGES = 16
NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.\-]{0,47}$")

DEFAULTS: dict[str, Any] = {
    "port": "",
    "baud": proto.DEFAULT_BAUD,
    "poll_interval_sec": 0.2,
    "read_profile": proto.DEFAULT_READ_PROFILE,
    "fft_periodic": False,
    "fft_interval_sec": 300.0,
    "fft_averages": 1,
    "fft_velocity_min_hz": DEFAULT_VELOCITY_MIN_HZ,
    "sensors": [],
}


def default_config() -> dict[str, Any]:
    return copy.deepcopy(DEFAULTS)


def _int(value: Any, label: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{label} must be an integer")
    if isinstance(value, str):
        text = value.strip().lower()
        value = int(text, 16) if text.startswith("0x") else int(text)
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if not isinstance(value, int):
        raise ValueError(f"{label} must be an integer")
    return value


def parse_address(value: Any) -> int:
    address = _int(value, "address")
    if not proto.valid_address(address):
        raise ValueError(f"address must be 0x{proto.MIN_ADDRESS:02X}..0x{proto.MAX_ADDRESS:02X}")
    return address


def parse_baud(value: Any) -> int:
    baud = _int(value, "baud")
    if baud not in proto.BAUD_CODES:
        raise ValueError(f"baud must be one of {', '.join(map(str, proto.SUPPORTED_BAUDS))}")
    return baud


def parse_fft_interval(value: Any) -> float:
    try:
        interval = float(value)
    except (TypeError, ValueError):
        raise ValueError("fft_interval_sec must be a number") from None
    if not MIN_FFT_INTERVAL_SEC <= interval <= MAX_FFT_INTERVAL_SEC:
        raise ValueError(f"fft_interval_sec must be {MIN_FFT_INTERVAL_SEC:g}..{MAX_FFT_INTERVAL_SEC:g}")
    return interval


def parse_fft_averages(value: Any) -> int:
    averages = _int(value, "fft_averages")
    if not MIN_FFT_AVERAGES <= averages <= MAX_FFT_AVERAGES:
        raise ValueError(f"fft_averages must be {MIN_FFT_AVERAGES}..{MAX_FFT_AVERAGES}")
    return averages


def parse_name(value: Any) -> str:
    name = str(value or "").strip()
    if not NAME_PATTERN.match(name):
        raise ValueError("name must be 1-48 characters: letters, digits, space, '_', '.', '-'")
    return name


def parse_sensor(item: Any) -> dict[str, Any]:
    if not isinstance(item, dict):
        raise ValueError("sensor must be an object")
    address = parse_address(item.get("address"))
    name = parse_name(item.get("name") or f"wtvb01-{address:02x}")
    return {"address": address, "name": name, "enabled": bool(item.get("enabled", True))}


def parse_velocity_min_hz(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError("fft_velocity_min_hz must be a number") from None
    if number == 0.0:  # Normalize the former automatic setting in saved configurations.
        return DEFAULT_VELOCITY_MIN_HZ
    if number not in VELOCITY_RMS_MIN_HZ_CHOICES:
        raise ValueError(f"fft_velocity_min_hz must be one of {', '.join(f'{c:g}' for c in VELOCITY_RMS_MIN_HZ_CHOICES)}")
    return number


def validate(data: Any) -> dict[str, Any]:
    """Return a normalized copy or raise ValueError."""
    if not isinstance(data, dict):
        raise ValueError("configuration must be an object")
    config = default_config()
    port = str(data.get("port") or "").strip()
    if port and not (port.startswith("/dev/") or port.startswith(SIM_PORT_PREFIX)):
        raise ValueError(f"port must be a /dev path or {SIM_PORT_PREFIX}...")
    config["port"] = port
    config["baud"] = parse_baud(data.get("baud", config["baud"]))
    try:
        poll = float(data.get("poll_interval_sec", config["poll_interval_sec"]))
    except (TypeError, ValueError):
        raise ValueError("poll_interval_sec must be a number") from None
    if not MIN_POLL_SEC <= poll <= MAX_POLL_SEC:
        raise ValueError(f"poll_interval_sec must be {MIN_POLL_SEC}..{MAX_POLL_SEC}")
    config["poll_interval_sec"] = poll
    profile = str(data.get("read_profile", config["read_profile"]))
    if profile not in proto.READ_PROFILES:
        raise ValueError(f"read_profile must be one of {', '.join(proto.READ_PROFILES)}")
    config["read_profile"] = profile
    # Periodic capture is switched by fft_periodic and keeps its interval while stopped.
    # Older files stored only fft_interval_sec, with 0 meaning off.
    raw_interval = data.get("fft_interval_sec", config["fft_interval_sec"])
    legacy_off = isinstance(raw_interval, (int, float)) and not isinstance(raw_interval, bool) and raw_interval == 0
    config["fft_interval_sec"] = DEFAULTS["fft_interval_sec"] if legacy_off else parse_fft_interval(raw_interval)
    periodic = data.get("fft_periodic", "fft_interval_sec" in data)
    if not isinstance(periodic, bool):
        raise ValueError("fft_periodic must be true or false")
    config["fft_periodic"] = False if legacy_off else periodic
    config["fft_averages"] = parse_fft_averages(data.get("fft_averages", config["fft_averages"]))
    config["fft_velocity_min_hz"] = parse_velocity_min_hz(data.get("fft_velocity_min_hz", config["fft_velocity_min_hz"]))
    sensors = data.get("sensors", [])
    if not isinstance(sensors, list) or len(sensors) > MAX_SENSORS:
        raise ValueError(f"sensors must be a list of at most {MAX_SENSORS}")
    parsed = [parse_sensor(item) for item in sensors]
    addresses = [s["address"] for s in parsed]
    names = [s["name"] for s in parsed]
    if len(set(addresses)) != len(addresses):
        raise ValueError("sensor addresses must be unique")
    if len(set(names)) != len(names):
        raise ValueError("sensor names must be unique")
    config["sensors"] = sorted(parsed, key=lambda s: s["address"])
    return config


def load(path: Path) -> tuple[dict[str, Any], str | None]:
    """Return (config, error). A missing file is not an error."""
    try:
        return validate(json.loads(path.read_text(encoding="utf-8"))), None
    except FileNotFoundError:
        return default_config(), None
    except (OSError, ValueError) as exc:
        return default_config(), f"{path}: {exc}"


def save(path: Path, config: dict[str, Any]) -> dict[str, Any]:
    normalized = validate(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", dir=path.parent, delete=False, encoding="utf-8") as stream:
        json.dump(normalized, stream, indent=2)
        stream.write("\n")
        temporary = Path(stream.name)
    os.replace(temporary, path)
    return normalized
